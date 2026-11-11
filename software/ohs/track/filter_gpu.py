"""The tracking filter (:func:`ohs.app.pipeline.tracking_depth`) on the GPU.

:class:`TrackingFilterGpu` gives the same depth map as ``tracking_depth(depth, sigma_px,
work_size=...)`` when the work size divides the input size (the pipeline's case: half the width and
height at both qualities), in four OpenCL kernels instead of about a dozen full-image passes on the
CPU. The steps are the CPU function's:

1. ``tf_down``, at the work size: each pixel of the work image checks its ``f x f`` block of input
   pixels for depth steps (3x3 Sobel, scale 1/8, reflected border: ``|grad z| <= slope * z``) and
   is valid only where the whole block is. A valid pixel takes the block's mean depth.
2. ``tf_blur_h`` and ``tf_blur_v``: the normalised Gaussian convolution of the valid depth, a row
   pass and a column pass with OpenCV's own kernel (``cv2.getGaussianKernel``) and a zero border.
   The column pass also erodes the validity mask. The CPU function measures a pixel's distance to
   the nearest invalid pixel with ``cv2.distanceTransform`` (L2, 5x5 mask: a chamfer distance, the
   image border counting as invalid). Here every pixel checks the offsets whose chamfer distance is
   within the radius, from a table built with ``cv2.distanceTransform`` itself. Pixels the smoothing
   moved by more than ``max_change * z`` are dropped, as on the CPU.
3. ``tf_up``, at the input size: bilinear upsampling with OpenCV's pixel-centre convention and
   border clamping, valid where every neighbour with a nonzero weight is.

Only float rounding differs from the CPU: on the simulator's depth maps about 0.01 % of the pixels
change validity and the values agree to within 1e-5 mm. Speed (1280x800 in, 640x400 work size):
see ``VALIDATION.md``, "GPU tuning".
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

__all__ = ["TrackingFilterGpu"]

_SRC = r"""
// OpenCL C 1.2. tracking_depth() of ohs/app/pipeline.py for an input f times the work size.
inline float dref(__global const float *d, int x, int y, const int w, const int h)
{
    x = x < 0 ? -x : (x >= w ? 2 * w - 2 - x : x);  // BORDER_REFLECT_101, as cv2.Sobel
    y = y < 0 ? -y : (y >= h ? 2 * h - 2 - y : y);
    return d[y * w + x];
}

inline int step_free(__global const float *d, const int x, const int y, const int w, const int h, const float slope)
{
    const float c = d[y * w + x];
    if (!(c > 0.0f)) return 0;
    const float a00 = dref(d, x - 1, y - 1, w, h), a01 = dref(d, x, y - 1, w, h), a02 = dref(d, x + 1, y - 1, w, h);
    const float a10 = dref(d, x - 1, y, w, h), a12 = dref(d, x + 1, y, w, h);
    const float a20 = dref(d, x - 1, y + 1, w, h), a21 = dref(d, x, y + 1, w, h), a22 = dref(d, x + 1, y + 1, w, h);
    const float gx = ((a02 + 2.0f * a12 + a22) - (a00 + 2.0f * a10 + a20)) * 0.125f;
    const float gy = ((a20 + 2.0f * a21 + a22) - (a00 + 2.0f * a01 + a02)) * 0.125f;
    return sqrt(gx * gx + gy * gy) <= c * slope;  // NaN anywhere: not valid
}

__kernel void tf_down(__global const float *d, const int w0, const int h0, const int f, const int w, const int h,
                      const float slope, __global float *dd, __global float *vf)
{
    const int X = get_global_id(0), Y = get_global_id(1);
    if (X >= w || Y >= h) return;
    float sum = 0.0f;
    int ok = 1;
    for (int b = 0; b < f && ok; ++b)
        for (int a = 0; a < f; ++a) {
            const int x = X * f + a, y = Y * f + b;
            if (!step_free(d, x, y, w0, h0, slope)) { ok = 0; break; }
            sum += d[y * w0 + x];
        }
    dd[Y * w + X] = ok ? sum / (float)(f * f) : 0.0f;
    vf[Y * w + X] = ok ? 1.0f : 0.0f;
}

__kernel void tf_blur_h(__global const float *dd, __global const float *vf, const int w, const int h,
                        __constant float *g, const int r, __global float *nh, __global float *dh)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= w || y >= h) return;
    float n = 0.0f, e = 0.0f;
    for (int i = -r; i <= r; ++i) {
        const int xx = x + i;
        if (xx < 0 || xx >= w) continue;  // BORDER_CONSTANT 0
        n += g[i + r] * dd[y * w + xx];
        e += g[i + r] * vf[y * w + xx];
    }
    nh[y * w + x] = n;
    dh[y * w + x] = e;
}

__kernel void tf_blur_v(__global const float *nh, __global const float *dh, __global const float *dd,
                        __global const float *vf, const int w, const int h, __constant float *g, const int r,
                        __constant int *elem, const int n_elem, const float max_change,
                        __global float *res, __global uchar *keep)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= w || y >= h) return;
    float n = 0.0f, e = 0.0f;
    for (int j = -r; j <= r; ++j) {
        const int yy = y + j;
        if (yy < 0 || yy >= h) continue;
        n += g[j + r] * nh[yy * w + x];
        e += g[j + r] * dh[yy * w + x];
    }
    const float out = n / fmax(e, 1e-6f);
    const float z = dd[y * w + x];
    int k = vf[y * w + x] > 0.0f && fabs(out - z) <= max_change * z;
    for (int q = 0; q < n_elem && k; ++q) {  // erosion: no invalid pixel (or border) within the radius
        const int xx = x + elem[2 * q], yy = y + elem[2 * q + 1];
        k = xx >= 0 && xx < w && yy >= 0 && yy < h && vf[yy * w + xx] > 0.0f;
    }
    res[y * w + x] = k ? out : 0.0f;
    keep[y * w + x] = (uchar)k;
}

inline void lin(const int x, const float inv_f, const int w, int *s, float *t)
{
    float fx = ((float)x + 0.5f) * inv_f - 0.5f;  // cv2.resize INTER_LINEAR, pixel centres
    int sx = (int)floor(fx);
    fx -= (float)sx;
    if (sx < 0) { sx = 0; fx = 0.0f; }
    if (sx >= w - 1) { sx = w - 1; fx = 0.0f; }
    *s = sx;
    *t = fx;
}

__kernel void tf_up(__global const float *res, __global const uchar *keep, const int w, const int h,
                    const int w0, const int h0, const float inv_f, __global float *out)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= w0 || y >= h0) return;
    int sx, sy;
    float fx, fy;
    lin(x, inv_f, w, &sx, &fx);
    lin(y, inv_f, h, &sy, &fy);
    const int sx1 = min(sx + 1, w - 1), sy1 = min(sy + 1, h - 1);
    int ok = keep[sy * w + sx] && (fx == 0.0f || keep[sy * w + sx1]);
    if (fy != 0.0f) ok = ok && keep[sy1 * w + sx] && (fx == 0.0f || keep[sy1 * w + sx1]);
    float v = 0.0f;
    if (ok) {
        const float r0 = res[sy * w + sx] * (1.0f - fx) + res[sy * w + sx1] * fx;
        const float r1 = res[sy1 * w + sx] * (1.0f - fx) + res[sy1 * w + sx1] * fx;
        v = r0 * (1.0f - fy) + r1 * fy;
    }
    out[y * w0 + x] = v;
}
"""
_I, _F = np.int32, np.float32


def _gaussian(sigma: float) -> np.ndarray:
    """OpenCV's kernel for ``GaussianBlur(..., (0, 0), sigma)`` on float images."""
    ksize = int(round(sigma * 4 * 2 + 1)) | 1
    return cv2.getGaussianKernel(ksize, sigma, cv2.CV_32F).ravel().astype(np.float32)


def _erosion_offsets(radius: float) -> np.ndarray:
    """Offsets (dx, dy) whose ``cv2.distanceTransform`` (L2, 5x5 mask) distance is at most ``radius``."""
    k = int(np.ceil(radius)) + 2
    img = np.ones((2 * k + 1, 2 * k + 1), np.uint8)
    img[k, k] = 0
    dist = cv2.distanceTransform(img, cv2.DIST_L2, 5)
    ys, xs = np.nonzero(dist <= radius)
    order = np.argsort(dist[ys, xs], kind="stable")  # the nearest first: an invalid pixel stops the loop early
    return np.stack([xs[order] - k, ys[order] - k], axis=1).astype(np.int32)


class TrackingFilterGpu:
    """``tracking_depth(depth, sigma_px, erode_mult, max_change, edge_slope, work_size)`` on the GPU.

    ``in_size`` and ``work_size`` are ``(width, height)``; the input must be an integer multiple of
    the work size (:meth:`supports`). ``edge_slope`` ``None`` is the CPU function's default (0.5 % at
    1280 px width, scaled with the width).
    """

    def __init__(self, dev: Any, in_size: tuple[int, int], work_size: tuple[int, int], sigma_px: float,
                 erode_mult: float = 2.0, max_change: float = 0.01, edge_slope: float | None = None) -> None:
        from ohs.app.pipeline import NOMINAL_WIDTH  # noqa: PLC0415

        if not self.supports(in_size, work_size) or sigma_px <= 0:
            raise ValueError(f"no GPU tracking filter for {in_size} -> {work_size} with sigma {sigma_px}")
        self.dev, self.cl = dev, dev.cl
        cl = self.cl
        self.w0, self.h0 = int(in_size[0]), int(in_size[1])
        self.w, self.h = int(work_size[0]), int(work_size[1])
        self.f = self.w0 // self.w
        self.slope = (0.005 * NOMINAL_WIDTH / self.w0) if edge_slope is None else float(edge_slope)
        s = float(sigma_px) * self.w / self.w0
        g = _gaussian(s)
        self.r = len(g) // 2
        el = _erosion_offsets(float(erode_mult) * s)
        self.n_elem = len(el)
        self.max_change = float(max_change)
        self.queue = dev.new_queue()
        prg = dev.program(_SRC)
        self._down = cl.Kernel(prg, "tf_down")
        self._down.set_scalar_arg_dtypes([None, _I, _I, _I, _I, _I, _F, None, None])
        self._bh = cl.Kernel(prg, "tf_blur_h")
        self._bh.set_scalar_arg_dtypes([None, None, _I, _I, None, _I, None, None])
        self._bv = cl.Kernel(prg, "tf_blur_v")
        self._bv.set_scalar_arg_dtypes([None, None, None, None, _I, _I, None, _I, None, _I, _F, None, None])
        self._up = cl.Kernel(prg, "tf_up")
        self._up.set_scalar_arg_dtypes([None, None, _I, _I, _I, _I, _F, None])
        mf, ctx = cl.mem_flags, dev.ctx
        n0, n = self.w0 * self.h0 * 4, self.w * self.h * 4
        self._in = cl.Buffer(ctx, mf.READ_ONLY, size=n0)
        self._out = cl.Buffer(ctx, mf.WRITE_ONLY, size=n0)
        self._work = [cl.Buffer(ctx, mf.READ_WRITE, size=n) for _ in range(5)]  # dd, vf, nh, dh, res
        self._keep = cl.Buffer(ctx, mf.READ_WRITE, size=self.w * self.h)
        self._g = cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=g)
        self._el = cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=np.ascontiguousarray(el.ravel()))

    @staticmethod
    def supports(in_size: tuple[int, int], work_size: tuple[int, int]) -> bool:
        """True when the input is ``f`` times the work size in both directions (``f`` >= 1)."""
        w0, h0 = int(in_size[0]), int(in_size[1])
        w, h = int(work_size[0]), int(work_size[1])
        return w > 1 and h > 1 and w0 % w == 0 and h0 % h == 0 and w0 // w == h0 // h

    def __call__(self, depth: np.ndarray) -> np.ndarray:
        return self.finish(self.start(depth))

    def start(self, depth: Any) -> tuple:
        """Queue the filter of ``depth`` and return at once; :meth:`finish` of the returned value
        waits for the map. One filter at a time.

        ``depth`` is the map, or a device buffer of this filter's device that holds it (``float32``,
        the input size): then nothing is uploaded. The buffer must already be written (its queue
        done) and must stay as it is until :meth:`finish`.
        """
        cl, q = self.cl, self.queue
        if isinstance(depth, cl.Buffer):
            if depth.size != self.w0 * self.h0 * 4:
                raise ValueError(f"the depth buffer has {depth.size} bytes, expected {self.w0 * self.h0 * 4}")
            src, hold = depth, None
        else:
            d = np.ascontiguousarray(depth, dtype=np.float32)
            if d.shape != (self.h0, self.w0):
                raise ValueError(f"depth has shape {d.shape}, expected {(self.h0, self.w0)}")
            # the copy's event is kept until finish: pyopencl waits for one that is dropped
            src, hold = self._in, (d, cl.enqueue_copy(q, self._in, d, is_blocking=False))
        dd, vf, nh, dh, res = self._work
        ws, w0s = (self.w, self.h), (self.w0, self.h0)
        self._down(q, ws, None, src, self.w0, self.h0, self.f, self.w, self.h, self.slope, dd, vf)
        self._bh(q, ws, None, dd, vf, self.w, self.h, self._g, self.r, nh, dh)
        self._bv(q, ws, None, nh, dh, dd, vf, self.w, self.h, self._g, self.r, self._el, self.n_elem,
                 self.max_change, res, self._keep)
        self._up(q, w0s, None, res, self._keep, self.w, self.h, self.w0, self.h0, 1.0 / self.f, self._out)
        out = np.empty((self.h0, self.w0), np.float32)
        ev = cl.enqueue_copy(q, out, self._out, is_blocking=False)
        q.flush()  # the device starts now
        return out, ev, hold

    def finish(self, pending: tuple) -> np.ndarray:
        """The filtered map of :meth:`start` (waits for the device, without holding Python's lock)."""
        out, ev, _hold = pending
        self.cl.wait_for_events([ev])  # the queue is in order: the upload and the kernels are done too
        return out

    def close(self) -> None:
        """Release the GPU buffers."""
        for b in (self._in, self._out, *self._work, self._keep, self._g, self._el):
            b.release()
        self._work = []
