"""GPU stereo depth: census semi-global matching in OpenCL, the GPU twin of :mod:`ohs.vision.depth`.

:class:`~ohs.vision.depth.DepthEstimator` delegates to :class:`GpuStereoMatcher` when
:func:`ohs.compute.get_device` finds a GPU (``DepthParams.backend``, see that module). The contract is
the CPU one: rectified grayscale ``uint8`` pair in, ``float32`` depth in mm along the rectified left
optical axis out, ``0`` = invalid; the disparity range comes from
:func:`~ohs.vision.depth.disparity_range`, and the validity rules, the left strip and the
post-filters are the same. One kernel source serves every vendor (OpenCL C 1.2, no extensions).

Pipeline (all on the GPU; only the images go up and the depth comes down)
-------------------------------------------------------------------------
1. **Census transform**, ternary, on the image after a 3x3 binomial blur (``census_blur``; it
   halves the sensor noise and keeps most of a dot): over a 9x7 window, each neighbour gives two
   bits, "brighter than the centre by more than ``census_threshold`` grey levels" (4) and "darker
   by more than it". The threshold matters on the dot pattern: between the sparse dots (about
   10 px apart at 1280x800) a plain census encodes sensor noise, which makes random costs; above
   about 2.5 noise sigmas the dark background is "similar" in both images and the dots carry the
   match. Too high a threshold loses the dim dots of dark or distant surfaces (8 grey levels
   without the blur lost a third of a dark car panel at 640x400). The matching cost is the
   Hamming distance (0-124).
2. **Texture support**: a pixel is "textured" when its census has at least ``support_bits`` (4)
   bits set, which noise alone almost never does; depth is kept only where at least
   ``support_fraction`` (20 %) of a ``support_window`` square (15 px at 1280 px width, 9 px at
   640, about one and a half dot spacings) is textured. Without it, the path costs carry depth
   from the dots into dot-free areas next to them (below the projector's field, beside a laser
   line): smooth, plausible and wrong by 0.3-1 mm, and fixed to the camera, which made the
   frame-to-model tracking slide on the car panel. SGBM produces noisy depth there instead, which
   the pipeline's tracking filter rejects.
3. **Semi-global matching**, 4 paths (left-right, right-left, top-down, bottom-up), penalties
   ``p1`` / ``p2``. Costs are computed on the fly from the census images, never stored. Each path
   line is a team of ``ND / 16`` work-items that hold 16 disparities each (16-bit vector math);
   neighbours and the minimum are exchanged in local memory, one barrier per step, and the next
   step's inputs are prefetched. The vertical kernel stores the top-down path costs (8 bit), adds
   the bottom-up ones and keeps the sum relative to its per-pixel minimum (8 bit, saturated; the
   minimum is stored apart, so the reconstruction is exact wherever the result is decided). The
   horizontal kernel does the same along rows and then picks the disparity.
4. **Winner-take-all** as OpenCV's SGBM: uniqueness ratio (``uniqueness_ratio``; a tie at zero
   cost, which only a texture-free window gives, fails it too), parabola sub-pixel fit (1/16 px),
   and the winners registered per right-image pixel for the **left-right check**
   (``disp12_max_diff``; both the floor and the ceiling must fail). Unlike SGBM, a winner at
   either end of the search range is dropped: the match may lie beyond it (typically in the left
   strip, whose true match is outside the right image), and :func:`disparity_range`'s margin puts
   those disparities outside the depth limits anyway. Pixels without texture support are dropped
   here.
5. **3x3 median** and **speckle filter** (``speckle_window_size``, ``speckle_range``): exact
   connected components on the GPU (runs per row, then union-find with ``atomic_min`` across
   rows), the same rule as ``cv2.filterSpeckles``.
6. **Validity** as the CPU path: disparity inside the search range and the match window inside the
   right image (``u - d >= block_size // 2``); with ``pad_left=False`` the SGBM strip
   ``u < minDisparity + numDisparities`` is invalid too.
7. **Sub-pixel refinement** (``subpixel_refine``): the same gain-invariant 1-D Lucas-Kanade step as
   the CPU (``_LKRefiner``: sigma-1 pre-blur, local normalisation over ``refine_norm_radius``,
   Gaussian window ``refine_window_sigma``, steps above ``refine_max_step`` or in textureless
   windows rejected).
8. **Depth** ``z = f B / d`` with ``[min_depth_mm, max_depth_mm]``. The division is correctly
   rounded (``-cl-fp32-correctly-rounded-divide-sqrt``), so :meth:`GpuStereoMatcher.depth` equals
   ``DepthEstimator.depth_from_disparity(disparity)`` bit for bit; a device without that option
   converts on the CPU.

Not on the GPU: ``DepthParams.wls`` (needs OpenCV's SGBM; the estimator uses the CPU) and
``mode`` (an SGBM setting, ignored). ``normalize=True`` runs the CPU's local contrast
normalisation before the census.

Accuracy (simulated ``sphere_on_plane`` and ``car_panel`` frames, realistic lens model, against the
true depth; in brackets the CPU SGBM path with the pipeline's settings):

* 1280x800 (8 frames): median 0.32 mm (0.37), p95 1.4 mm (2.1), |error| > 2 mm 1.8 % (5.6 %),
  disparity error > 1 px 0.9 % (4.1 %), valid 89.8 % (93.6 %); depth bias -0.002 mm (+0.12).
  The pixels the CPU keeps and the GPU drops lie where no dot falls, and half of them are off by
  more than 2 mm on the CPU.
* 640x400 from 1280x800 frames (the live scale): median 0.41 mm (0.46), p95 2.1 mm (2.7),
  > 2 mm 5.3 % (8.8 %), > 1 px 1.1 % (2.8 %), valid 90.1 % (96.9 %); natively rendered 640x400
  frames: p95 2.6 mm (3.4), > 1 px 0.7 % (4.5 %).
* Tracking (15 simulated scans, ``sphere_on_plane`` and ``car_panel`` at both resolutions, with
  the Open3D and the GPU tracking models): the maximum trajectory error with this depth is at or
  below SGBM's on almost every scan, on the car panel by up to 40 %.

The fourth path matters: without the bottom-up pass (OpenCV's 3-way set) the depth is biased by
+0.15 mm, as the CPU's is.

Speed on a Radeon 680M (integrated, 1280x800, 208 disparities): about 66 ms per frame wall-clock,
62 ms of it in kernels (the CPU path: about 134 ms); 640x400 (112 disparities): about 14 ms, 11.5 ms
in kernels (CPU: 21 ms). The two aggregation kernels take about 90 % of the time; they are bound by
latency and instruction issue, not by memory bandwidth (removing all their DRAM traffic changes
nothing), and their local-memory layouts are padded against bank conflicts. Device memory: about
``W * H * (2 * ND + 64)`` bytes (450 MB at 1280x800), allocated at the first frame.

One matcher per thread (it owns a command queue, kernels and buffers).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from ohs.vision.depth import DepthParams

__all__ = ["GpuTuning", "GpuStereoMatcher", "gpu_unsupported"]

log = logging.getLogger(__name__)

# keep in step with ohs.vision.depth._LKRefiner
_LK_EPS2 = 16.0
_LK_MIN_TEXTURE = 0.02
MIN_SIZE = 32  # px; smaller images go to the CPU path

_SOURCE = r"""
/* OpenHandScan GPU stereo (ohs/vision/depth_gpu.py): census semi-global matching. OpenCL C 1.2.
   Compile-time constants (-D): W H image size; ND DMIN disparity count and minimum; DPT
   disparities per work-item; NV NHR path lines per work-group (vertical, horizontal); CRX CRY CT
   census half-window and threshold; P1 P2 CMAX SGM penalties and largest cost; UNIQ LRMAX
   uniqueness ratio and left-right tolerance (< 0: off); INVALID invalid disparity code; RING ring
   size (power of two > ND); TSP padded team size; HALFW LSTRIP validity rules; NR WR LK radii. */
#define TS (ND / DPT)
#define BIG 0x3FFF
#define DMAX (DMIN + ND - 1)
#define PAD(i) ((i) + ((i) >> 4))   /* one spare slot per 16: work-items 16 entries apart use other banks */

#if DPT == 16
typedef ushort16 vec_t;
typedef short16 svec_t;
typedef uchar16 bvec_t;
#define VL(o, p) vload16(o, p)
#define VS(v, o, p) vstore16(v, o, p)
#define TO_B(v) convert_uchar16(v)
#define TO_V(v) convert_ushort16(v)
#define SHL(v, a) ((vec_t)((a), (v).s0, (v).s1, (v).s2, (v).s3, (v).s4, (v).s5, (v).s6, (v).s7, (v).s8, (v).s9, (v).sa, (v).sb, (v).sc, (v).sd, (v).se))
#define SHR(v, b) ((vec_t)((v).s1, (v).s2, (v).s3, (v).s4, (v).s5, (v).s6, (v).s7, (v).s8, (v).s9, (v).sa, (v).sb, (v).sc, (v).sd, (v).se, (v).sf, (b)))
#define VFIRST(v) ((v).s0)
#define VLAST(v) ((v).sf)
#define IOTA ((vec_t)(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15))
#define FROM_ARR(c) ((vec_t)(c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8], c[9], c[10], c[11], c[12], c[13], c[14], c[15]))
inline uint hmin(const vec_t v) { ushort8 a = min(v.lo, v.hi); ushort4 b = min(a.lo, a.hi); ushort2 c = min(b.lo, b.hi); return min(c.x, c.y); }
#elif DPT == 8
typedef ushort8 vec_t;
typedef short8 svec_t;
typedef uchar8 bvec_t;
#define VL(o, p) vload8(o, p)
#define VS(v, o, p) vstore8(v, o, p)
#define TO_B(v) convert_uchar8(v)
#define TO_V(v) convert_ushort8(v)
#define SHL(v, a) ((vec_t)((a), (v).s0, (v).s1, (v).s2, (v).s3, (v).s4, (v).s5, (v).s6))
#define SHR(v, b) ((vec_t)((v).s1, (v).s2, (v).s3, (v).s4, (v).s5, (v).s6, (v).s7, (b)))
#define VFIRST(v) ((v).s0)
#define VLAST(v) ((v).s7)
#define IOTA ((vec_t)(0, 1, 2, 3, 4, 5, 6, 7))
#define FROM_ARR(c) ((vec_t)(c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]))
inline uint hmin(const vec_t v) { ushort4 b = min(v.lo, v.hi); ushort2 c = min(b.lo, b.hi); return min(c.x, c.y); }
#else
#error "DPT must be 8 or 16"
#endif

/* reflect-101 border (OpenCV's default) for an offset smaller than the image */
inline int refl(int i, const int n)
{
    if (i < 0) i = -i;
    if (i >= n) i = 2 * n - 2 - i;
    return i;
}

/* the CPU path's validity rule for a raw disparity (1/16 px) at column x */
inline bool disp_ok(const short v, const int x)
{
    return v >= DMIN * 16 && v <= DMAX * 16 && ((float)x - (float)v * 0.0625f) >= (float)HALFW && x >= LSTRIP;
}

/* ------------------------------------------------------------------------------- census
   Both images in one launch (dimension 2). The left instance also resets the per-frame
   accumulators of the left-right check and of the speckle filter. With CBLUR the census compares
   the image after a 3x3 binomial blur (x16 fixed point; the threshold is in grey levels). */
#define TX 16
#define TY 16
#define CTW (TX + 2 * CRX)
#define CTH (TY + 2 * CRY)
#define CBW (CTW + 2 * CBLUR)
#define CBH (CTH + 2 * CBLUR)
__kernel __attribute__((reqd_work_group_size(TX, TY, 1)))
void census(__global const uchar* restrict il, __global const uchar* restrict ir,
            __global ulong2* restrict ol, __global ulong2* restrict orr,
            __global uint* restrict d2, __global int* restrict ssize, __global uchar* restrict tex)
{
    const bool right = get_global_id(2) != 0;
    __global const uchar* img = right ? ir : il;
    __global ulong2* out = right ? orr : ol;
    const int lx = get_local_id(0), ly = get_local_id(1);
    const int x = get_global_id(0), y = get_global_id(1);
    const int bx = get_group_id(0) * TX - CRX - CBLUR, by = get_group_id(1) * TY - CRY - CBLUR;
#if CBLUR
    __local uchar raw[CBH][CBW];
    __local ushort tile[CTH][CTW];
    for (int i = ly * TX + lx; i < CBW * CBH; i += TX * TY) {
        const int ty = i / CBW, tx = i - ty * CBW;
        raw[ty][tx] = img[clamp(by + ty, 0, H - 1) * W + clamp(bx + tx, 0, W - 1)];
    }
    barrier(CLK_LOCAL_MEM_FENCE);
    for (int i = ly * TX + lx; i < CTW * CTH; i += TX * TY) {
        const int ty = i / CTW, tx = i - ty * CTW;
        const int r0 = raw[ty][tx] + 2 * raw[ty][tx + 1] + raw[ty][tx + 2];
        const int r1 = raw[ty + 1][tx] + 2 * raw[ty + 1][tx + 1] + raw[ty + 1][tx + 2];
        const int r2 = raw[ty + 2][tx] + 2 * raw[ty + 2][tx + 1] + raw[ty + 2][tx + 2];
        tile[ty][tx] = (ushort)(r0 + 2 * r1 + r2);
    }
    #define CTS (16 * CT)
#else
    __local uchar tile[CTH][CTW];
    for (int i = ly * TX + lx; i < CTW * CTH; i += TX * TY) {
        const int ty = i / CTW, tx = i - ty * CTW;
        tile[ty][tx] = img[clamp(by + ty, 0, H - 1) * W + clamp(bx + tx, 0, W - 1)];
    }
    #define CTS CT
#endif
    barrier(CLK_LOCAL_MEM_FENCE);
    if (x >= W || y >= H) return;
    const int c = tile[ly + CRY][lx + CRX];
    ulong hi = 0, lo = 0;
    int bit = 0;
    #pragma unroll
    for (int dy = 0; dy <= 2 * CRY; ++dy) {
        #pragma unroll
        for (int dx = 0; dx <= 2 * CRX; ++dx) {
            if (dx == CRX && dy == CRY) continue;
            const int q = tile[ly + dy][lx + dx];
            hi |= (ulong)(q > c + CTS) << bit;
            lo |= (ulong)(q < c - CTS) << bit;
            ++bit;
        }
    }
    out[y * W + x] = (ulong2)(hi, lo);
    if (!right) {
        d2[y * W + x] = 0xFFFFFFFFu;
        ssize[y * W + x] = 0;
        tex[y * W + x] = popcount(hi | lo) >= TB ? 1 : 0;   /* the window has structure, not just noise */
    }
}

/* texture support: at least SNEED pixels with census texture in the SW x SW window (replicated
   border). Depth elsewhere would only be filled in by the path costs from textured neighbours. */
#define ST 16
#define SH (SW / 2)
#define STW (ST + 2 * SH)
__kernel __attribute__((reqd_work_group_size(ST, ST, 1)))
void support(__global const uchar* restrict tex, __global uchar* restrict sup)
{
    const int lx = get_local_id(0), ly = get_local_id(1);
    const int x0 = get_group_id(0) * ST, y0 = get_group_id(1) * ST;
    __local uchar tt[STW][STW];
    __local ushort hs[STW][ST];
    for (int i = ly * ST + lx; i < STW * STW; i += ST * ST) {
        const int ty = i / STW, tx = i - ty * STW;
        tt[ty][tx] = tex[clamp(y0 - SH + ty, 0, H - 1) * W + clamp(x0 - SH + tx, 0, W - 1)];
    }
    barrier(CLK_LOCAL_MEM_FENCE);
    for (int i = ly * ST + lx; i < STW * ST; i += ST * ST) {
        const int ty = i / ST, tx = i - ty * ST;
        ushort n = 0;
        for (int k = 0; k < SW; ++k) n += tt[ty][tx + k];
        hs[ty][tx] = n;
    }
    barrier(CLK_LOCAL_MEM_FENCE);
    const int x = x0 + lx, y = y0 + ly;
    if (x >= W || y >= H) return;
    int n = 0;
    for (int k = 0; k < SW; ++k) n += hs[ly + k][lx];
    sup[y * W + x] = n >= SNEED ? 1 : 0;
}

inline ushort census_cost(const ulong2 a, const ulong2 b)
{
    return (ushort)(popcount(a.x ^ b.x) + popcount(a.y ^ b.y));
}

inline vec_t sgm_step(const vec_t C, const vec_t L, const ushort left, const ushort right, const ushort mprev)
{
    const vec_t v = min(min(L, (vec_t)(mprev + P2)), min(SHL(L, left), SHR(L, right)) + (vec_t)(P1));
    return C + v - (vec_t)(mprev);
}

/* team reductions over the per-work-item partials, padded to TSP with neutral values */
#if TSP == 16
inline ushort tmin16(__local const ushort* p) { return (ushort)hmin(VL(0, p)); }
inline uint tmin32(__local const uint* p)
{
    const uint16 v = vload16(0, p);
    const uint8 a = min(v.lo, v.hi); const uint4 b = min(a.lo, a.hi); const uint2 c = min(b.lo, b.hi);
    return min(c.x, c.y);
}
inline uint tany(__local const uchar* p)
{
    const uchar16 v = vload16(0, p);
    const uchar8 a = v.lo | v.hi; const uchar4 b = a.lo | a.hi; const uchar2 c = b.lo | b.hi;
    return c.x | c.y;
}
#else
inline ushort tmin16(__local const ushort* p) { ushort m = p[0]; for (int i = 1; i < TS; ++i) m = min(m, p[i]); return m; }
inline uint tmin32(__local const uint* p) { uint m = p[0]; for (int i = 1; i < TS; ++i) m = min(m, p[i]); return m; }
inline uint tany(__local const uchar* p) { uint m = 0; for (int i = 0; i < TS; ++i) m |= p[i]; return m; }
#endif

/* ------------------------------------------------------------------ vertical paths (2)
   One team of TS work-items per column, NV columns per work-group. The right census row segment
   the group needs (columns x0 - DMAX .. x0 + NV - 1 - DMIN) is staged in local memory; the next
   row's segment, left census and path costs are loaded while the current row is processed. The
   top-down pass keeps its path costs in `scratch` (8 bit); the bottom-up pass adds them and
   stores the sum relative to its per-pixel minimum (8 bit, saturated), the minimum in `svmin`. */
#define NTV (TS * NV)
#define SEGV (ND + NV - 1)
#define NPRE ((SEGV + NTV - 1) / NTV)
__kernel __attribute__((reqd_work_group_size(TS, NV, 1)))
void sgm_vertical(__global const ulong2* restrict cl, __global const ulong2* restrict cr,
                  __global uchar* restrict scratch, __global uchar* restrict sv, __global ushort* restrict svmin)
{
    const int t = get_local_id(0), c = get_local_id(1);
    const int lid = c * TS + t;
    const int x0 = get_group_id(1) * NV;
    const int x = x0 + c;
    const bool act = x < W;
    const int xs0 = x0 - DMAX;
    const int k0 = t * DPT;
    const int sbase = c + (ND - 1) - k0;
    const int xr0 = x - DMIN - k0;
    const bool all_in = xr0 - (DPT - 1) >= 0;
    __local ulong2 seg[2][PAD(SEGV) + 1];
    __local ushort bnd[2][NV][TS][2];
    __local ushort mn[2][NV][TSP];
    __local ushort mv[2][NV][TSP];
    for (int i = lid; i < SEGV; i += NTV) {
        const int xc = xs0 + i;
        seg[0][PAD(i)] = (xc >= 0 && xc < W) ? cr[xc] : (ulong2)(0);
    }
    for (int i = TS + t; i < TSP; i += TS) {
        for (int bb = 0; bb < 2; ++bb) { mn[bb][c][i] = BIG; mv[bb][c][i] = BIG; }
    }
    ulong2 a = act ? cl[x] : (ulong2)(0);
    bvec_t tb = (bvec_t)(0);
    barrier(CLK_LOCAL_MEM_FENCE);
    vec_t L = (vec_t)(0);
    ushort left = BIG, right = BIG, mprev = 0;
    for (int s = 0; s < 2 * H; ++s) {
        const int b = s & 1;
        const bool down = s < H;
        const int y = down ? s : 2 * H - 1 - s;
        const int s1 = s + 1;
        const bool more = s1 < 2 * H;
        const int y1 = s1 < H ? s1 : 2 * H - 1 - s1;
        ulong2 pre[NPRE];
        ulong2 a1 = (ulong2)(0);
        bvec_t tb1 = (bvec_t)(0);
        if (more) {
            #pragma unroll
            for (int i = 0; i < NPRE; ++i) {
                const int ii = lid + i * NTV;
                const int xc = xs0 + ii;
                pre[i] = (ii < SEGV && xc >= 0 && xc < W) ? cr[y1 * W + xc] : (ulong2)(0);
            }
            if (act) {
                a1 = cl[y1 * W + x];
                if (s1 > H) tb1 = VL(0, scratch + ((size_t)y1 * W + x) * ND + k0);
            }
        }
        ushort cc[DPT];
        if (all_in) {
            #pragma unroll
            for (int j = 0; j < DPT; ++j) cc[j] = census_cost(a, seg[b][PAD(sbase - j)]);
        } else {
            #pragma unroll
            for (int j = 0; j < DPT; ++j) cc[j] = (xr0 - j >= 0) ? census_cost(a, seg[b][PAD(sbase - j)]) : (ushort)CMAX;
        }
        const vec_t C = FROM_ARR(cc);
        L = (s == 0 || s == H) ? C : sgm_step(C, L, left, right, mprev);
        if (s1 == H) tb1 = TO_B(L);    /* the first bottom-up step needs the last top-down row */
        const size_t o = ((size_t)y * W + x) * ND + k0;
        vec_t sum = L;
        if (down) {
            if (act) VS(TO_B(L), 0, scratch + o);
        } else {
            sum = TO_V(tb) + L;
            mv[b][c][t] = (ushort)hmin(sum);
        }
        bnd[b][c][t][0] = VFIRST(L);
        bnd[b][c][t][1] = VLAST(L);
        mn[b][c][t] = (ushort)hmin(L);
        if (more) {
            #pragma unroll
            for (int i = 0; i < NPRE; ++i) {
                const int ii = lid + i * NTV;
                if (ii < SEGV) seg[b ^ 1][PAD(ii)] = pre[i];
            }
        }
        barrier(CLK_LOCAL_MEM_FENCE);
        left = t > 0 ? bnd[b][c][t - 1][1] : (ushort)BIG;
        right = t < TS - 1 ? bnd[b][c][t + 1][0] : (ushort)BIG;
        mprev = tmin16(&mn[b][c][0]);
        if (!down) {
            const ushort m2 = tmin16(&mv[b][c][0]);
            if (act) {
                VS(TO_B(min(sum - (vec_t)(m2), (vec_t)(255))), 0, sv + o);
                if (t == 0) svmin[y * W + x] = m2;
            }
        }
        a = a1;
        tb = tb1;
    }
}

/* ---------------------------------------- horizontal paths (2) + winner-take-all + uniqueness
   One team per row, NHR rows per work-group. The right census of the columns in the search
   window lives in a ring buffer in local memory (one new column per step). The left-to-right
   pass keeps its path costs in `scratch`; the right-to-left pass adds everything up and picks the
   disparity: the uniqueness test and the parabola run one step later in the work-item that owns
   the winner, which writes the raw disparity (1/16 px) and registers the winner for the
   left-right check in `d2` (atomic_min of cost << 16 | disparity, per right-image pixel). */
/* ring slot of a column (RING = ND + 16 entries, a multiple of 16) */
#define RSLOT(c) ((c) % RING)
__kernel __attribute__((reqd_work_group_size(TS, NHR, 1)))
void sgm_horizontal(__global const ulong2* restrict cl, __global const ulong2* restrict cr,
                    __global const uchar* restrict sv, __global const ushort* restrict svmin,
                    __global uchar* restrict scratch, __global short* restrict disp, __global uint* restrict d2)
{
    const int t = get_local_id(0), r = get_local_id(1);
    const int y = get_group_id(1) * NHR + r;
    const bool act = y < H;
    const int k0 = t * DPT;
    const int row = (act ? y : 0) * W;
    __local ulong2 ring[NHR][PAD(RING) + 1];
    __local ushort bnd[2][NHR][TS][2];
    __local ushort sb[2][NHR][TS][2];
    __local ushort mn[2][NHR][TSP];
    __local uint red[2][NHR][TSP];
    __local uchar flg[2][NHR][TSP];
    for (int i = t; i < ND; i += TS) {
        const int xc = -DMAX + i;
        if (xc >= 0 && xc < W) ring[r][PAD(RSLOT(xc))] = cr[row + xc];
    }
    for (int i = TS + t; i < TSP; i += TS) {
        for (int bb = 0; bb < 2; ++bb) { mn[bb][r][i] = BIG; red[bb][r][i] = 0xFFFFFFFFu; flg[bb][r][i] = 0; }
    }
    ulong2 a = cl[row];
    bvec_t lr_ = (bvec_t)(0), sv_ = (bvec_t)(0);
    ushort svm_ = 0;
    barrier(CLK_LOCAL_MEM_FENCE);
    vec_t L = (vec_t)(0), S = (vec_t)(0);
    ushort left = BIG, right = BIG, mprev = 0;
    uchar viol = 0;
    bool owner = false;
    int pend_d = INVALID, pend_x = 0;
    uint pend_best = 0;
    const vec_t kv = IOTA + (vec_t)(k0);
    for (int s = 0; s <= 2 * W; ++s) {
        const bool fwd = s < W;
        const bool tail = s == 2 * W;
        const int x = fwd ? s : 2 * W - 1 - s;
        const int b = s & 1;
        /* prefetch for the next step */
        const int s1 = s + 1;
        const int x1 = s1 < W ? s1 : 2 * W - 1 - s1;
        ulong2 a1 = (ulong2)(0), rc = (ulong2)(0);
        bvec_t lr1 = (bvec_t)(0), sv1 = (bvec_t)(0);
        ushort svm1 = 0;
        const int xn = s1 < W ? x1 - DMIN : x1 - DMAX;    /* column entering the window at the next step */
        const bool load_rc = t == 0 && s1 < 2 * W && s1 != W && xn >= 0 && xn < W;
        if (s1 < 2 * W) {
            a1 = cl[row + x1];
            if (load_rc) rc = cr[row + xn];
            if (s1 >= W && act) {
                const size_t o1 = ((size_t)row + x1) * ND + k0;
                if (s1 > W) lr1 = VL(0, scratch + o1);
                sv1 = VL(0, sv + o1);
                svm1 = svmin[row + x1];
            }
        }
        if (!tail) {
            const int xr0 = x - DMIN - k0;
            const int base = RSLOT(max(xr0, 0)) + RING;   /* slot of column xr0 - j: (base - j) mod RING */
            ushort cc[DPT];
            if (xr0 - (DPT - 1) >= 0) {
                #pragma unroll
                for (int j = 0; j < DPT; ++j) {
                    const int sl = base - j >= RING ? base - j - RING : base - j;
                    cc[j] = census_cost(a, ring[r][PAD(sl)]);
                }
            } else {
                #pragma unroll
                for (int j = 0; j < DPT; ++j) {
                    const int sl = base - j >= RING ? base - j - RING : base - j;
                    cc[j] = (xr0 - j >= 0) ? census_cost(a, ring[r][PAD(sl)]) : (ushort)CMAX;
                }
            }
            const vec_t C = FROM_ARR(cc);
            L = (s == 0 || s == W) ? C : sgm_step(C, L, left, right, mprev);
            if (s1 == W) lr1 = TO_B(L);    /* the first right-to-left step needs the last left-to-right column */
            if (fwd) {
                if (act) VS(TO_B(L), 0, scratch + ((size_t)row + x) * ND + k0);
            } else {
                S = TO_V(sv_) + (vec_t)(svm_) + TO_V(lr_) + L;
                sb[b][r][t][0] = VFIRST(S);
                sb[b][r][t][1] = VLAST(S);
                const uint m = hmin(S);
                const uint idx = hmin(select((vec_t)(0xFFFF), IOTA, S == (vec_t)(m)));
                red[b][r][t] = (m << 16) | (uint)(k0 + idx);
            }
            bnd[b][r][t][0] = VFIRST(L);
            bnd[b][r][t][1] = VLAST(L);
            mn[b][r][t] = (ushort)hmin(L);
        }
        flg[b][r][t] = viol;
        if (load_rc) ring[r][PAD(RSLOT(xn))] = rc;
        barrier(CLK_LOCAL_MEM_FENCE);
        if (!tail) {
            left = t > 0 ? bnd[b][r][t - 1][1] : (ushort)BIG;
            right = t < TS - 1 ? bnd[b][r][t + 1][0] : (ushort)BIG;
            mprev = tmin16(&mn[b][r][0]);
        }
        /* finish the previous pixel: unique unless some work-item flagged it, and not at an end of
           the search range (the match may lie beyond it; such disparities are outside the depth
           limits anyway, see disparity_range) */
        if (s > W && owner && act) {
            short out = INVALID;
            const uint kbp = pend_best & 0xFFFFu;
            if (!tany(&flg[b][r][0]) && kbp > 0 && kbp < ND - 1) {
                out = (short)pend_d;
                const int dbest = DMIN + (int)(pend_best & 0xFFFFu);
                const int q = pend_x - dbest;
                if (q >= 0) atomic_min(&d2[row + q], (pend_best & 0xFFFF0000u) | (uint)dbest);
            }
            disp[row + pend_x] = out;
        }
        if (!fwd && !tail) {
            const uint best = tmin32(&red[b][r][0]);
            const uint minS = best >> 16;
            const uint kb = best & 0xFFFFu;
            /* uniqueness (OpenCV): fails if S[k] * (100 - UNIQ) < minS * 100 for some |k - kb| > 1;
               census costs can tie at exactly 0 (no texture at all), and such a tie fails too */
            const ushort thr = (ushort)max((minS * 100u + (99u - UNIQ)) / (100u - UNIQ), UNIQ > 0 ? 1u : 0u);
            const svec_t far_ = (kv > (vec_t)(kb + 1)) | ((kv + (vec_t)(1)) < (vec_t)(kb));
            viol = any(far_ & (S < (vec_t)(thr))) ? 1 : 0;
            owner = (int)kb >= k0 && (int)kb < k0 + DPT;
            if (owner) {
                /* parabola through the costs at kb - 1, kb, kb + 1 (the neighbours' edge values via sb) */
                const int d = (int)kb;
                int d16 = d * 16;
                if (d > 0 && d < ND - 1) {
                    const int sm = d - 1 >= k0 ? (int)hmin(select((vec_t)(0xFFFF), S, kv == (vec_t)(d - 1))) : (int)sb[b][r][t - 1][1];
                    const int sp = d + 1 < k0 + DPT ? (int)hmin(select((vec_t)(0xFFFF), S, kv == (vec_t)(d + 1))) : (int)sb[b][r][t + 1][0];
                    const int denom2 = max(sm + sp - 2 * (int)minS, 1);
                    d16 += (int)floor((float)(sm - sp) * 8.0f / (float)denom2 + 0.5f);
                }
                pend_d = d16 + DMIN * 16;
                pend_best = best;
                pend_x = x;
            }
        }
        a = a1;
        if (s1 >= W) { lr_ = lr1; sv_ = sv1; svm_ = svm1; }
    }
}

/* ------------------------------------------------- left-right check + 3x3 median (replicate)
   The check is OpenCV's: a disparity is dropped when both its floor and its ceiling land on right
   pixels whose winning disparity differs by more than LRMAX. Then the median of the 3x3
   neighbourhood (cv2.medianBlur's border), as SGBM does before its speckle filter. */
inline short lr_at(__global const short* restrict din, __global const uint* restrict d2,
                   __global const uchar* restrict sup, const int x, const int y)
{
    const int row = y * W;
    short dv = din[row + x];
    if (!sup[row + x]) return INVALID;
#if LRMAX >= 0  /* a compile-time switch: a runtime test of the constant draws a warning from NVIDIA's compiler */
    if (dv != INVALID) {
        const int dl = dv >> 4, du = (dv + 15) >> 4;
        const int xl = x - dl, xu = x - du;
        bool bad_l = false, bad_u = false;
        if (xl >= 0 && xl < W) { const uint e = d2[row + xl]; if (e != 0xFFFFFFFFu) bad_l = abs((int)(e & 0xFFFFu) - dl) > LRMAX; }
        if (xu >= 0 && xu < W) { const uint e = d2[row + xu]; if (e != 0xFFFFFFFFu) bad_u = abs((int)(e & 0xFFFFu) - du) > LRMAX; }
        if (bad_l && bad_u) dv = INVALID;
    }
#endif
    return dv;
}

#define SORT2(a, b) { const short t_ = min(a, b); b = max(a, b); a = t_; }
inline short median9(short p0, short p1, short p2, short p3, short p4, short p5, short p6, short p7, short p8)
{
    SORT2(p1, p2); SORT2(p4, p5); SORT2(p7, p8); SORT2(p0, p1); SORT2(p3, p4); SORT2(p6, p7);
    SORT2(p1, p2); SORT2(p4, p5); SORT2(p7, p8); SORT2(p0, p3); SORT2(p5, p8); SORT2(p4, p7);
    SORT2(p3, p6); SORT2(p1, p4); SORT2(p2, p5); SORT2(p4, p7); SORT2(p4, p2); SORT2(p6, p4);
    SORT2(p4, p2);
    return p4;
}

__kernel void lr_median(__global const short* restrict din, __global const uint* restrict d2,
                        __global const uchar* restrict sup, __global short* restrict dout)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= W || y >= H) return;
    const int xm = max(x - 1, 0), xp = min(x + 1, W - 1);
    const int ym = max(y - 1, 0), yp = min(y + 1, H - 1);
    dout[y * W + x] = median9(lr_at(din, d2, sup, xm, ym), lr_at(din, d2, sup, x, ym), lr_at(din, d2, sup, xp, ym),
                              lr_at(din, d2, sup, xm, y), lr_at(din, d2, sup, x, y), lr_at(din, d2, sup, xp, y),
                              lr_at(din, d2, sup, xm, yp), lr_at(din, d2, sup, x, yp), lr_at(din, d2, sup, xp, yp));
}

/* the plain 3x3 median (for tests against cv2.medianBlur) */
__kernel void median3(__global const short* restrict src, __global short* restrict dst)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= W || y >= H) return;
    const int xm = max(x - 1, 0), xp = min(x + 1, W - 1);
    const int rm = max(y - 1, 0) * W, r0 = y * W, rp = min(y + 1, H - 1) * W;
    dst[r0 + x] = median9(src[rm + xm], src[rm + x], src[rm + xp], src[r0 + xm], src[r0 + x], src[r0 + xp],
                          src[rp + xm], src[rp + x], src[rp + xp]);
}

/* --------------------------------------- speckle filter: connected components (union-find)
   Pixels are linked to their 4-neighbours when both are valid and differ by at most `maxdiff`
   (1/16 px), as cv2.filterSpeckles. Runs along each row get a label (their first pixel), runs are
   merged across rows with lock-free union-find (atomic_min on the parent), the component sizes
   are summed per root, and components of at most `maxsize` pixels are invalidated. */
inline int find_root(__global volatile int* parent, int a)
{
    int p = parent[a];
    while (p != a) { a = p; p = parent[a]; }
    return a;
}

inline void unite(__global volatile int* parent, int a, int b)
{
    bool done = false;
    while (!done) {
        a = find_root(parent, a);
        b = find_root(parent, b);
        if (a < b) { const int old = atomic_min(&parent[b], a); done = old == b; b = old; }
        else if (b < a) { const int old = atomic_min(&parent[a], b); done = old == a; a = old; }
        else done = true;
    }
}

inline bool linked(const short a, const short b, const int maxdiff)
{
    return a != INVALID && b != INVALID && abs((int)a - (int)b) <= maxdiff;
}

__kernel void spk_rows(__global const short* restrict d, __global int* restrict label,
                       __global int* restrict parent, __global int* restrict runlen, const int maxdiff)
{
    const int y = get_global_id(0);
    if (y >= H) return;
    const int row = y * W;
    int start = -1;
    short prev = INVALID;
    for (int x = 0; x < W; ++x) {
        const short v = d[row + x];
        if (v == INVALID) {
            if (start >= 0) runlen[start] = row + x - start;
            start = -1;
            label[row + x] = -1;
        } else {
            if (start < 0 || !linked(v, prev, maxdiff)) {
                if (start >= 0) runlen[start] = row + x - start;
                start = row + x;
                parent[start] = start;
            }
            label[row + x] = start;
        }
        prev = v;
    }
    if (start >= 0) runlen[start] = row + W - start;
}

__kernel void spk_merge(__global const short* restrict d, __global const int* restrict label,
                        __global volatile int* parent, const int maxdiff)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= W || y < 1 || y >= H) return;
    const int p = y * W + x, q = p - W;
    const int lp = label[p], lq = label[q];
    if (lp < 0 || lq < 0 || !linked(d[p], d[q], maxdiff)) return;
    /* the same two runs were already linked by the pixel pair on the left */
    if (x > 0 && label[p - 1] == lp && label[q - 1] == lq && linked(d[p - 1], d[q - 1], maxdiff)) return;
    unite(parent, lp, lq);
}

__kernel void spk_count(__global const int* restrict label, __global volatile int* parent,
                        __global const int* restrict runlen, __global int* restrict ssize)
{
    const int p = get_global_id(0);
    if (p >= W * H || label[p] != p) return;
    const int root = find_root(parent, p);
    atomic_add(&ssize[root], runlen[p]);
    parent[p] = root;
}

__kernel void spk_apply(__global short* restrict d, __global const int* restrict label,
                        __global const int* restrict parent, __global const int* restrict ssize, const int maxsize)
{
    const int p = get_global_id(0);
    if (p >= W * H) return;
    const int l = label[p];
    if (l >= 0 && ssize[parent[l]] <= maxsize) d[p] = INVALID;
}

/* ------------------------------------------ sub-pixel refinement (gain-invariant 1-D LK)
   The CPU's _LKRefiner: normalised images (5x5 Gaussian sigma 1, then (x - mean) / sqrt(var +
   eps^2) over a (2 NR + 1)^2 box), the right one warped by the disparity, symmetric gradient g and
   residual e accumulated over a Gaussian window (WR taps each side), one Gauss-Newton step.
   Reflect-101 borders throughout, as OpenCV; with symmetric kernels the halo can be filtered
   directly. */
#define LT 16
#define LH (2 + NR)
#define LTW (LT + 2 * LH)
#define LBW (LT + 2 * NR)
__kernel __attribute__((reqd_work_group_size(LT, LT, 1)))
void lk_prep(__global const uchar* restrict il, __global const uchar* restrict ir,
             __global float* restrict ol, __global float* restrict orr, __constant float* g5, const float eps2)
{
    const bool right = get_global_id(2) != 0;
    __global const uchar* img = right ? ir : il;
    __global float* out = right ? orr : ol;
    const int lx = get_local_id(0), ly = get_local_id(1);
    const int x0 = get_group_id(0) * LT, y0 = get_group_id(1) * LT;
    __local float raw[LTW][LTW];
    __local float xb[LBW][LBW];
    for (int i = ly * LT + lx; i < LTW * LTW; i += LT * LT) {
        const int ty = i / LTW, tx = i - ty * LTW;
        raw[ty][tx] = (float)img[refl(y0 - LH + ty, H) * W + refl(x0 - LH + tx, W)];
    }
    barrier(CLK_LOCAL_MEM_FENCE);
    for (int i = ly * LT + lx; i < LBW * LBW; i += LT * LT) {
        const int ty = i / LBW, tx = i - ty * LBW;
        float acc = 0.0f;
        for (int dy = 0; dy < 5; ++dy) {
            float h = 0.0f;
            for (int dx = 0; dx < 5; ++dx) h += g5[dx] * raw[ty + dy][tx + dx];
            acc += g5[dy] * h;
        }
        xb[ty][tx] = acc;
    }
    barrier(CLK_LOCAL_MEM_FENCE);
    const int x = x0 + lx, y = y0 + ly;
    if (x >= W || y >= H) return;
    float s = 0.0f, s2 = 0.0f;
    for (int dy = 0; dy <= 2 * NR; ++dy) {
        for (int dx = 0; dx <= 2 * NR; ++dx) {
            const float v = xb[ly + dy][lx + dx];
            s += v;
            s2 += v * v;
        }
    }
    const float inv = 1.0f / (float)((2 * NR + 1) * (2 * NR + 1));
    const float mu = s * inv;
    const float var = max(s2 * inv - mu * mu, 0.0f) + eps2;
    out[y * W + x] = (xb[ly + NR][lx + NR] - mu) / sqrt(var);
}

/* the right normalised image sampled at x - d (bilinear, replicated border; d = 0 where invalid) */
inline float warp_at(__global const float* restrict rn, __global const short* restrict d, const int x, const int y)
{
    const short v = d[y * W + x];
    const float xs = (float)x - (disp_ok(v, x) ? (float)v * 0.0625f : 0.0f);
    const float fx = floor(xs);
    const float f = xs - fx;
    const int xi = (int)fx;
    return (1.0f - f) * rn[y * W + clamp(xi, 0, W - 1)] + f * rn[y * W + clamp(xi + 1, 0, W - 1)];
}

__kernel void lk_grad(__global const float* restrict ln, __global const float* restrict rn,
                      __global const short* restrict d, __global float* restrict ge, __global float* restrict g2)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= W || y >= H) return;
    const int p = y * W + x;
    const int xm = refl(x - 1, W), xp = refl(x + 1, W);
    float g = 0.25f * ((ln[y * W + xp] - ln[y * W + xm]) + (warp_at(rn, d, xp, y) - warp_at(rn, d, xm, y)));
    if (!disp_ok(d[p], x)) g = 0.0f;
    ge[p] = g * (ln[p] - warp_at(rn, d, x, y));
    g2[p] = g * g;
}

#define WT 16
#define WTW (WT + 2 * WR)
__kernel __attribute__((reqd_work_group_size(WT, WT, 1)))
void lk_window(__global const float* restrict ga, __global const float* restrict gb,
               __global float* restrict oa, __global float* restrict ob, __constant float* w)
{
    const int lx = get_local_id(0), ly = get_local_id(1);
    const int x0 = get_group_id(0) * WT, y0 = get_group_id(1) * WT;
    __local float ta[WTW][WTW], tb[WTW][WTW];
    __local float ha[WTW][WT], hb[WTW][WT];
    for (int i = ly * WT + lx; i < WTW * WTW; i += WT * WT) {
        const int ty = i / WTW, tx = i - ty * WTW;
        const int q = refl(y0 - WR + ty, H) * W + refl(x0 - WR + tx, W);
        ta[ty][tx] = ga[q];
        tb[ty][tx] = gb[q];
    }
    barrier(CLK_LOCAL_MEM_FENCE);
    for (int i = ly * WT + lx; i < WTW * WT; i += WT * WT) {
        const int ty = i / WT, tx = i - ty * WT;
        float sa = 0.0f, sb = 0.0f;
        for (int k = 0; k <= 2 * WR; ++k) {
            sa += w[k] * ta[ty][tx + k];
            sb += w[k] * tb[ty][tx + k];
        }
        ha[ty][tx] = sa;
        hb[ty][tx] = sb;
    }
    barrier(CLK_LOCAL_MEM_FENCE);
    const int x = x0 + lx, y = y0 + ly;
    if (x >= W || y >= H) return;
    float sa = 0.0f, sb = 0.0f;
    for (int k = 0; k <= 2 * WR; ++k) {
        sa += w[k] * ha[ly + k][lx];
        sb += w[k] * hb[ly + k][lx];
    }
    oa[y * W + x] = sa;
    ob[y * W + x] = sb;
}

/* refined disparity (NaN = invalid) and depth (mm, 0 = invalid); the division is correctly rounded
   when the program is built with -cl-fp32-correctly-rounded-divide-sqrt */
__kernel void finish(__global const short* restrict d, __global const float* restrict num,
                     __global const float* restrict den, __global float* restrict disp_out,
                     __global float* restrict depth_out, const int refine, const float max_step,
                     const float min_texture, const float fb, const float zmin, const float zmax)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= W || y >= H) return;
    const int p = y * W + x;
    const short v = d[p];
    const bool ok = disp_ok(v, x);
    float dd = (float)v * 0.0625f;
    if (refine && ok) {
        const float dn = den[p];
        const float delta = num[p] / max(dn, 1e-6f);
        if (fabs(delta) < max_step && dn > min_texture) dd = dd - delta;
    }
    disp_out[p] = ok ? dd : NAN;
    const float z = fb / dd;
    depth_out[p] = (ok && isfinite(z) && z >= zmin && z <= zmax) ? z : 0.0f;
}
"""


@dataclass(frozen=True)
class GpuTuning:
    """Settings of the GPU matcher that have no SGBM counterpart in :class:`DepthParams`.

    The defaults were chosen on simulated frames against the true depth (module docstring).
    """

    census_rx: int = 4  # census window half-width (9 px)
    census_ry: int = 3  # census window half-height (7 px)
    census_threshold: int = 4  # grey levels (after the blur): smaller differences count as "similar"
    census_blur: bool = True  # 3x3 binomial blur before the census (halves the sensor noise)
    support_window: int | None = None  # px; None: 15 at 1280 px width, scaled with the width (9 at 640)
    support_bits: int = 4  # census bits that make a pixel "textured"
    support_fraction: float = 0.2  # share of textured pixels the window needs (0: no support test)
    p1: int = 10  # SGM penalty for a 1 px disparity change (census costs are 0..124)
    p2: int = 100  # SGM penalty for a larger jump; cost + p2 must stay <= 255 (8-bit path costs)
    lines_v: int = 16  # columns per work-group in the vertical aggregation
    lines_h: int = 2  # rows per work-group in the horizontal aggregation
    dpt: int = 16  # disparities per work-item (8 or 16; the disparity count must be a multiple)


def gpu_unsupported(params: DepthParams) -> str | None:
    """Why the GPU matcher cannot honour ``params`` (``None``: it can)."""
    if params.wls:
        return "the WLS filter (DepthParams.wls) needs OpenCV's SGBM"
    return None


class GpuStereoMatcher:
    """Census SGM on one OpenCL device, with the :class:`~ohs.vision.depth.DepthEstimator` contract.

    Parameters
    ----------
    dev:
        The :class:`ohs.compute.ComputeDevice`.
    K, baseline_mm:
        Rectified intrinsics at the processed resolution and the baseline (mm).
    params:
        :class:`DepthParams` (``wls`` is not supported, see :func:`gpu_unsupported`).
    min_disparity, num_disparities:
        The search range from :func:`~ohs.vision.depth.disparity_range`.
    profiling:
        Time every kernel (:attr:`last_timings_ms`); slightly slower.

    The OpenCL program is built and the device buffers are allocated at the first frame (and
    again when the image size changes). Not thread-safe: one matcher per thread.
    """

    def __init__(self, dev: Any, K: np.ndarray, baseline_mm: float, params: DepthParams, min_disparity: int,
                 num_disparities: int, tuning: GpuTuning | None = None, profiling: bool = False) -> None:
        reason = gpu_unsupported(params)
        if reason:
            raise ValueError(reason)
        self.dev = dev
        self.cl = dev.cl
        self.params = params
        self.tuning = tuning if tuning is not None else GpuTuning()
        K = np.asarray(K, dtype=np.float64).reshape(3, 3)
        self.f = float(K[0, 0])
        self.baseline = float(baseline_mm)
        self.min_disparity = int(min_disparity)
        self.num_disparities = int(num_disparities)
        t = self.tuning
        if t.dpt not in (8, 16) or self.num_disparities % t.dpt:
            raise ValueError(f"the disparity count ({self.num_disparities}) must be a multiple of {t.dpt}")
        nbits = (2 * t.census_rx + 1) * (2 * t.census_ry + 1) - 1
        if nbits > 64:
            raise ValueError("the census window has more than 64 neighbours")
        self._cmax = 2 * nbits
        if self._cmax + t.p2 > 255 or not 0 < t.p1 < t.p2:
            raise ValueError("need 0 < p1 < p2 and census cost + p2 <= 255")
        if self.min_disparity < 0 or (self.min_disparity + self.num_disparities) * 16 >= 32768:
            raise ValueError("disparity range outside the 16-bit fixed-point range")
        self.queue = dev.new_queue(profiling=profiling)
        self.profiling = bool(profiling)
        self.exact_division = bool(dev.device.single_fp_config & self.cl.device_fp_config.CORRECTLY_ROUNDED_DIVIDE_SQRT)
        s = float(params.refine_window_sigma)
        self._win = cv2.getGaussianKernel(int(2 * math.ceil(2.5 * s) + 1), s).astype(np.float32).ravel()
        self._g5 = cv2.getGaussianKernel(5, 1.0).astype(np.float32).ravel()
        self._shape: tuple[int, int] | None = None
        self._k: dict[str, Any] = {}
        self._b: dict[str, Any] = {}
        self._calls: list[tuple] = []
        self.last_timings_ms: dict[str, float] = {}

    @property
    def device_name(self) -> str:
        return str(self.dev.name)

    # ------------------------------------------------------------------ setup
    def resolved(self, w: int) -> tuple[int, int, int]:
        """``(census threshold, support window, textured pixels needed)`` for images ``w`` px wide."""
        t = self.tuning
        ct = int(t.census_threshold)
        sw = int(t.support_window) if t.support_window is not None else max(3, int(round(15.0 * w / 1280.0)) | 1)
        sw |= 1
        need = int(math.ceil(float(t.support_fraction) * sw * sw)) if t.support_fraction > 0 else 0
        return ct, sw, need

    def _options(self, w: int, h: int) -> list[str]:
        t, p = self.tuning, self.params
        ts = self.num_disparities // t.dpt
        ct, sw, need = self.resolved(w)
        lr = int(p.disp12_max_diff) if int(p.disp12_max_diff) > 0 else -1
        opts = {
            "W": w, "H": h, "ND": self.num_disparities, "DMIN": self.min_disparity, "DPT": t.dpt,
            "NV": t.lines_v, "NHR": t.lines_h, "CRX": t.census_rx, "CRY": t.census_ry, "CT": ct,
            "CBLUR": int(bool(t.census_blur)), "TB": int(t.support_bits), "SW": sw, "SNEED": need,
            "P1": t.p1, "P2": t.p2, "CMAX": self._cmax, "UNIQ": min(max(int(p.uniqueness_ratio), 0), 99), "LRMAX": lr,
            "INVALID": (self.min_disparity - 1) * 16, "RING": self.num_disparities + 16,
            "TSP": 16 if ts <= 16 else ts, "HALFW": max(int(p.block_size) // 2, 0),
            "LSTRIP": 0 if p.pad_left else self.min_disparity + self.num_disparities,
            "NR": int(p.refine_norm_radius), "WR": len(self._win) // 2,
        }
        out = [f"-D{k}={v}" for k, v in opts.items()]
        if self.exact_division:
            out.append("-cl-fp32-correctly-rounded-divide-sqrt")
        return out

    def _check_limits(self) -> None:
        t, d = self.tuning, self.dev.device
        ts = self.num_disparities // t.dpt
        need = max(ts * t.lines_v, ts * t.lines_h, 256)
        if need > int(d.max_work_group_size):
            raise RuntimeError(f"the GPU allows {d.max_work_group_size} work-items per group, the matcher needs {need}")
        for name in ("sgm_vertical", "sgm_horizontal", "lk_prep", "lk_window", "census"):
            k = self._k[name]
            lim = int(k.get_work_group_info(self.cl.kernel_work_group_info.WORK_GROUP_SIZE, d))
            want = {"sgm_vertical": ts * t.lines_v, "sgm_horizontal": ts * t.lines_h}.get(name, 256)
            if want > lim:
                raise RuntimeError(f"kernel {name} allows {lim} work-items per group on this GPU, it needs {want}")

    def _setup(self, h: int, w: int) -> None:
        if w < MIN_SIZE or h < MIN_SIZE:  # the filter halos assume images larger than themselves
            raise ValueError(f"images of {w}x{h} are too small for the GPU matcher (at least {MIN_SIZE}x{MIN_SIZE})")
        cl, t, p = self.cl, self.tuning, self.params
        prg = self.dev.program(_SOURCE, self._options(w, h))
        names = ("census", "support", "sgm_vertical", "sgm_horizontal", "lr_median", "median3", "spk_rows", "spk_merge",
                 "spk_count", "spk_apply", "lk_prep", "lk_grad", "lk_window", "finish")
        self._k = {n: cl.Kernel(prg, n) for n in names}
        self._check_limits()
        ctx, mf = self.dev.ctx, cl.mem_flags
        n = w * h
        nd = self.num_disparities

        def buf(nbytes: int) -> Any:
            return cl.Buffer(ctx, mf.READ_WRITE, size=int(nbytes))

        b = {
            "il": buf(n), "ir": buf(n), "cl": buf(16 * n), "cr": buf(16 * n),
            "scratch": buf(n * nd), "sv": buf(n * nd), "svmin": buf(2 * n), "d2": buf(4 * n),
            "raw": buf(2 * n), "disp16": buf(2 * n), "label": buf(4 * n), "parent": buf(4 * n),
            "runlen": buf(4 * n), "ssize": buf(4 * n), "tex": buf(n), "sup": buf(n),
            "ln": buf(4 * n), "rn": buf(4 * n), "ge": buf(4 * n), "g2": buf(4 * n), "num": buf(4 * n),
            "den": buf(4 * n), "disp": buf(4 * n), "depth": buf(4 * n),
            "g5": cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=self._g5),
            "win": cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=self._win),
        }
        self._lk_images = False
        if p.normalize:  # the census sees the contrast-normalised pair, the refinement the original one
            b["lkl"], b["lkr"] = buf(n), buf(n)
            self._lk_images = True
        self._b = b
        k = self._k
        ts = nd // t.dpt
        r16 = lambda v: int(math.ceil(v / 16)) * 16  # noqa: E731
        g2d, l2d = (r16(w), r16(h)), (16, 16)
        g3d, l3d = (r16(w), r16(h), 2), (16, 16, 1)
        lkl = b["lkl"] if self._lk_images else b["il"]
        lkr = b["lkr"] if self._lk_images else b["ir"]
        refine = bool(p.subpixel_refine)
        # (name, kernel, global, local, args): arguments are bound once, per frame only enqueued
        calls = [
            ("census", k["census"], g3d, l3d, (b["il"], b["ir"], b["cl"], b["cr"], b["d2"], b["ssize"], b["tex"])),
            ("support", k["support"], g2d, l2d, (b["tex"], b["sup"])),
            ("sgm_vertical", k["sgm_vertical"], (ts, int(math.ceil(w / t.lines_v)) * t.lines_v), (ts, t.lines_v),
             (b["cl"], b["cr"], b["scratch"], b["sv"], b["svmin"])),
            ("sgm_horizontal", k["sgm_horizontal"], (ts, int(math.ceil(h / t.lines_h)) * t.lines_h), (ts, t.lines_h),
             (b["cl"], b["cr"], b["sv"], b["svmin"], b["scratch"], b["raw"], b["d2"])),
            ("lr_median", k["lr_median"], g2d, l2d, (b["raw"], b["d2"], b["sup"], b["disp16"])),
        ]
        if int(p.speckle_window_size) > 0:
            maxdiff = np.int32(16 * int(p.speckle_range))
            n1 = (int(math.ceil(n / 256)) * 256,)
            calls += [
                ("spk_rows", k["spk_rows"], (int(math.ceil(h / 64)) * 64,), (64,),
                 (b["disp16"], b["label"], b["parent"], b["runlen"], maxdiff)),
                ("spk_merge", k["spk_merge"], g2d, l2d, (b["disp16"], b["label"], b["parent"], maxdiff)),
                ("spk_count", k["spk_count"], n1, (256,), (b["label"], b["parent"], b["runlen"], b["ssize"])),
                ("spk_apply", k["spk_apply"], n1, (256,),
                 (b["disp16"], b["label"], b["parent"], b["ssize"], np.int32(p.speckle_window_size))),
            ]
        if refine:
            calls += [
                ("lk_prep", k["lk_prep"], g3d, l3d, (lkl, lkr, b["ln"], b["rn"], b["g5"], np.float32(_LK_EPS2))),
                ("lk_grad", k["lk_grad"], g2d, l2d, (b["ln"], b["rn"], b["disp16"], b["ge"], b["g2"])),
                ("lk_window", k["lk_window"], g2d, l2d, (b["ge"], b["g2"], b["num"], b["den"], b["win"])),
            ]
        calls.append(("finish", k["finish"], g2d, l2d,
                      (b["disp16"], b["num"], b["den"], b["disp"], b["depth"], np.int32(1 if refine else 0),
                       np.float32(p.refine_max_step), np.float32(_LK_MIN_TEXTURE),
                       np.float32(self.f * self.baseline), np.float32(p.min_depth_mm), np.float32(p.max_depth_mm))))
        for _, kern, _, _, args in calls:
            kern.set_args(*args)
        self._calls = [(name, kern, gs, ls) for name, kern, gs, ls, _ in calls]
        self._shape = (h, w)
        mb = (2 * n * nd + 64 * n) / 2**20
        log.debug("GPU matcher ready: %dx%d, %d disparities from %d, about %.0f MB on %s",
                  w, h, nd, self.min_disparity, mb, self.device_name)

    # ------------------------------------------------------------------ run
    def _run(self, left: np.ndarray, right: np.ndarray, lk_left: np.ndarray | None, lk_right: np.ndarray | None,
             what: str) -> np.ndarray:
        cl, q = self.cl, self.queue
        h, w = left.shape
        if self._shape != (h, w):
            self._setup(h, w)
        b = self._b
        hold = [np.ascontiguousarray(left), np.ascontiguousarray(right)]  # alive until the queue is done
        cl.enqueue_copy(q, b["il"], hold[0], is_blocking=False)
        cl.enqueue_copy(q, b["ir"], hold[1], is_blocking=False)
        if self._lk_images:
            hold += [np.ascontiguousarray(lk_left), np.ascontiguousarray(lk_right)]
            cl.enqueue_copy(q, b["lkl"], hold[2], is_blocking=False)
            cl.enqueue_copy(q, b["lkr"], hold[3], is_blocking=False)
        events = [(name, cl.enqueue_nd_range_kernel(q, kern, gs, ls)) for name, kern, gs, ls in self._calls]
        out = np.empty((h, w), np.float32)
        cl.enqueue_copy(q, out, b[what], is_blocking=True)
        if self.profiling:
            self.last_timings_ms = {name: (e.profile.end - e.profile.start) * 1e-6 for name, e in events}
        del hold
        return out

    def disparity(self, left: np.ndarray, right: np.ndarray, lk_left: np.ndarray | None = None,
                  lk_right: np.ndarray | None = None) -> np.ndarray:
        """Disparity ``u_left - u_right`` in px (float32, NaN = invalid).

        ``left``/``right`` are what the census sees; ``lk_left``/``lk_right`` the images of the
        sub-pixel refinement (needed only with ``DepthParams.normalize``).
        """
        return self._run(left, right, lk_left, lk_right, "disp")

    def depth(self, left: np.ndarray, right: np.ndarray, lk_left: np.ndarray | None = None,
              lk_right: np.ndarray | None = None) -> np.ndarray | None:
        """Depth in mm (float32, 0 = invalid), or ``None`` when this device cannot divide exactly
        (the caller then converts :meth:`disparity` on the CPU)."""
        if not self.exact_division:
            return None
        return self._run(left, right, lk_left, lk_right, "depth")

    # ------------------------------------------------------------------ test hooks
    def filter_disparity(self, raw16: np.ndarray, median: bool = True) -> np.ndarray:
        """Run the 3x3 median (if ``median``) and the speckle filter on a raw ``int16`` disparity
        map (1/16 px, ``(min_disparity - 1) * 16`` = invalid); for tests against OpenCV."""
        cl, q = self.cl, self.queue
        h, w = raw16.shape
        if self._shape != (h, w):
            self._setup(h, w)
        b, k = self._b, self._k
        src = np.ascontiguousarray(raw16, dtype=np.int16)
        cl.enqueue_copy(q, b["raw"], src)
        r16 = lambda v: int(math.ceil(v / 16)) * 16  # noqa: E731
        if median:
            k["median3"](q, (r16(w), r16(h)), (16, 16), b["raw"], b["disp16"])
        else:
            cl.enqueue_copy(q, b["disp16"], b["raw"])
        cl.enqueue_fill_buffer(q, b["ssize"], np.int32(0), 0, 4 * w * h)
        for name, kern, gs, ls in self._calls:
            if name.startswith("spk_"):
                cl.enqueue_nd_range_kernel(q, kern, gs, ls)
        out = np.empty((h, w), np.int16)
        cl.enqueue_copy(q, out, b["disp16"])
        q.finish()
        return out
