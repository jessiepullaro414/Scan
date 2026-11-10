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
and at three depths (the spacing grows with the voxel size, so the samples per brick stay the same). Voxel ``(x, y, z)`` of brick ``b`` sits at world position ``(8 b + (x, y, z)) *
voxel_mm``, Open3D's convention for block resolution 8. Per voxel, 28 bytes:

- ``tsdf``: the mean of the truncated projective SDF ``min(d - z, trunc)`` (mm, camera z);
- ``wsum``: the sum of the averaging weights (1 per observation), and ``m2``: the sum of squared
  deviations from the mean (Welford): the scatter actually observed;
- ``s2``: the sum of the observations' variances ``min(sigma, trunc)^2`` (truncation caps how far
  one observation can move the mean);
- ``nobs``: the number of observations, and ``nlook``: the number of *independent looks*;
- ``luv``: the pixel of the last counted look.

**Independent looks.** The dots are fixed to the scanner, so a surface point keeps seeing the same
dots until its image position has moved by about a dot spacing, and frames that see the same dots
make nearly the same error (``VALIDATION.md``: a correlation of 0.4 between frames 0.1 s apart). A
look counts when the voxel's pixel is at least ``look_px`` from the last counted one: the dot
spacing, 10 px at 1280 px width.

**A voxel's standard deviation** (mm, along the camera z of its observations, so an upper bound
along the normal): ``sigma^2 = max(s2 / wsum^2 * nobs, m2 / wsum) / nlook``. The first term is
what the pixels' own sigmas predict for the mean (their mean variance), the second the scatter
actually observed; the larger one is divided by the independent looks, never by the correlated
frames. Marching cubes interpolates it to every vertex (:meth:`ConfidenceTsdf.extract_mesh`). On
the simulator 2 sigma covers 95-100 % of the vertices' true error, and the vertices it rates within
1 mm are measurably the better part of the mesh.

**Backends.** ``"gpu"``: OpenCL on :func:`ohs.compute.get_device` (one work-item per voxel of the
frame's bricks, so no voxel is written twice). ``"cpu"``: the same arithmetic in numpy, which is
also the tests' reference. ``"auto"`` picks the GPU when there is one.

**Meshing** copies the bricks into a CPU Open3D ``VoxelBlockGrid`` (block resolution 8). Its
``weight`` is ``nobs``, so ``weight_threshold`` keeps meaning "fused more than so many times", and
its ``color`` carries ``(sigma, nlook, wsum)``, which marching cubes interpolates like a colour
(Open3D scales colours by 1/255 on the way out; undone here).

**Speed** (1280x800, Radeon 680M): about 20-28 ms per frame, most of it the CPU's brick bookkeeping
(Open3D's CPU integration: 16-19 ms). The numpy twin needs about 0.5 s per frame, so without a GPU
the pipeline keeps Open3D (no confidence) unless asked for this model.

The interface mirrors :class:`ohs.track.markerless.MarkerlessTracker`'s model side (``integrate``,
``extract_mesh``, ``extract_points``, ``n_integrated``, ``reset``, ``close``), so the pipeline can use
either. Not thread-safe.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

__all__ = ["ConfidenceTsdf", "BRICK", "RECON_BACKENDS", "voxel_sigma", "pixel_weights"]

log = logging.getLogger(__name__)

BRICK = 8
NVOX = BRICK ** 3
RECON_BACKENDS = ("auto", "gpu", "cpu")
LOOK_PX_1280 = 10.0  # dot spacing (px) at 1280 px width: one independent look per move of this much
_OFF = 1 << 20  # brick key offset: +-1M bricks per axis
_FLOAT_CHANNELS = ("tsdf", "wsum", "m2", "s2", "nobs", "nlook")

_SRC = r"""
// OpenCL C 1.2. Sparse confidence TSDF of ohs/track/recon_tsdf.py (see its docstring).
// Brick slot s holds voxels s * 512 + (lz * 64 + ly * 8 + lx), one array per channel.
__kernel void recon_integrate(
    __global float *tsdf, __global float *wsum, __global float *m2, __global float *s2, __global float *nobs,
    __global float *nlook, __global uint *luv,
    __global const int *slots, __global const int *keys, const int n_active,
    __global const float *depth, __global const float *sigma, const int has_sigma, const int w, const int h,
    const float fx, const float fy, const float cx, const float cy, const float sk,
    const float r00, const float r01, const float r02,
    const float r10, const float r11, const float r12,
    const float r20, const float r21, const float r22,
    const float t0, const float t1, const float t2,
    const float voxel, const float trunc, const float depth_min, const float depth_max, const float look2)
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
    // the weight and variance of pixel_weights(): 1, and min(sigma, trunc)^2 (trunc^2 / 9 without sigmas)
    const float wt = 1.0f;
    float var = trunc * trunc / 9.0f;
    if (has_sigma) {
        const float sg = sigma[pix];
        const float sc = (isnan(sg) || isinf(sg)) ? trunc : fmin(sg, trunc);
        var = sc * sc;
    }
    const float x = fmin(sdf, trunc);
    const int a = slots[b] * 512 + l;
    const float W0 = wsum[a], m0 = tsdf[a];
    const float W1 = W0 + wt;
    const float m1 = m0 + (wt / W1) * (x - m0);
    m2[a] += wt * (x - m0) * (x - m1);
    s2[a] += wt * wt * var;
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
"""
_I, _F = np.int32, np.float32
_ARGS = [None] * 9 + [_I] + [None, None] + [_I, _I, _I] + [_F] * 5 + [_F] * 12 + [_F] * 5


def _encode(k: np.ndarray) -> np.ndarray:
    k = np.asarray(k, dtype=np.int64) + _OFF
    return (k[:, 0] << 42) | (k[:, 1] << 21) | k[:, 2]


def _decode(e: np.ndarray) -> np.ndarray:
    e = np.asarray(e, dtype=np.int64)
    return np.stack([(e >> 42) & 0x1FFFFF, (e >> 21) & 0x1FFFFF, e & 0x1FFFFF], axis=1) - _OFF


def voxel_sigma(wsum: np.ndarray, m2: np.ndarray, s2: np.ndarray, nobs: np.ndarray, nlook: np.ndarray) -> np.ndarray:
    """Standard deviation (mm) of each voxel's weighted mean; 0 where unobserved (module docstring):
    ``sqrt(max(s2 / wsum^2 * nobs, m2 / wsum) / max(nlook, 1))``."""
    W = np.asarray(wsum, dtype=np.float64)
    out = np.zeros(W.shape, dtype=np.float32)
    ok = W > 0
    Wk = W[ok]
    pred = np.asarray(s2, np.float64)[ok] / (Wk * Wk) * np.asarray(nobs, np.float64)[ok]
    emp = np.maximum(np.asarray(m2, np.float64)[ok], 0.0) / Wk
    out[ok] = np.sqrt(np.maximum(pred, emp) / np.maximum(np.asarray(nlook, np.float64)[ok], 1.0))
    return out


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
    initial_bricks, max_bricks:
        Pool size at the start, and its limit (bricks beyond it are dropped, with a warning).
    """

    def __init__(self, K: np.ndarray, image_size: tuple[int, int], voxel_mm: float = 1.0, trunc_mm: float = 4.0,
                 depth_min_mm: float = 120.0, depth_max_mm: float = 500.0, backend: str = "auto",
                 look_px: float | None = None, initial_bricks: int = 4096, max_bricks: int | None = None) -> None:
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
        self._img_bufs: Any = None
        self._list_cap = 0
        self._slot_buf = self._key_buf = None
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
    def reset(self) -> None:
        """Forget everything (the pool shrinks back to its initial size)."""
        self._enc_sorted = np.zeros(0, dtype=np.int64)
        self._slot_sorted = np.zeros(0, dtype=np.int64)
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
            # the kernel checks the raw depth and weighs each pixel itself (the rules of pixel_weights),
            # which saves several full-image passes on the CPU and one upload
            slots, keys = self._slots_for(self._frame_bricks(depth, T))
            if len(slots):
                sig = None if sigma_mm is None else np.ascontiguousarray(sigma_mm, dtype=np.float32)
                self._integrate_gpu(slots, keys, depth, sig, R_cw, t_cw)
        else:
            depth = np.where(np.isfinite(depth) & (depth >= self.depth_min_mm) & (depth <= self.depth_max_mm),
                             depth, np.float32(0.0)).astype(np.float32)
            wmap, vmap = pixel_weights(depth, sigma_mm, self.trunc_mm)
            slots, keys = self._slots_for(self._frame_bricks(depth, T))
            if len(slots):
                self._integrate_cpu(slots, keys, depth, wmap, vmap, R_cw, t_cw)
        self.n_integrated += 1

    def _integrate_cpu(self, slots: np.ndarray, keys: np.ndarray, depth: np.ndarray, wmap: np.ndarray,
                       vmap: np.ndarray, R_cw: np.ndarray, t_cw: np.ndarray) -> None:
        h, w = depth.shape
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
        good = (d > 0) & (d <= self.depth_max_mm) & (sdf >= -self.trunc_mm) & (wt > 0)
        sel, sdf, wt, ui, vi, pix = sel[good], sdf[good], wt[good], ui[good], vi[good], pix[good]
        a = (slots.astype(np.int64)[:, None] * NVOX + np.arange(NVOX)[None]).reshape(-1)[sel]
        b = self._bufs
        x = np.minimum(sdf, np.float32(self.trunc_mm))
        W0, m0 = b["wsum"][a], b["tsdf"][a]
        W1 = W0 + wt
        m1 = m0 + (wt / W1) * (x - m0)
        b["m2"][a] += wt * (x - m0) * (x - m1)
        b["s2"][a] += wt * wt * vmap.ravel()[pix]
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

    def _integrate_gpu(self, slots: np.ndarray, keys: np.ndarray, depth: np.ndarray, sigma: np.ndarray | None,
                       R_cw: np.ndarray, t_cw: np.ndarray) -> None:
        cl, q = self.cl, self.queue
        h, w = depth.shape
        mf = cl.mem_flags
        if self._img_bufs is None or self._img_bufs[0] != (h, w):
            if self._img_bufs is not None:
                for bb in self._img_bufs[1:]:
                    bb.release()
            self._img_bufs = ((h, w), *(cl.Buffer(self.dev.ctx, mf.READ_ONLY, size=h * w * 4) for _ in range(2)))
        _, dbuf, sbuf = self._img_bufs
        m = len(slots)
        if m > self._list_cap:
            for bb in (self._slot_buf, self._key_buf):
                if bb is not None:
                    bb.release()
            self._list_cap = max(m, 2 * self._list_cap, 1024)
            self._slot_buf = cl.Buffer(self.dev.ctx, mf.READ_ONLY, size=self._list_cap * 4)
            self._key_buf = cl.Buffer(self.dev.ctx, mf.READ_ONLY, size=self._list_cap * 12)
        hold = (depth, sigma, np.ascontiguousarray(slots, np.int32), np.ascontiguousarray(keys, np.int32))
        cl.enqueue_copy(q, dbuf, hold[0], is_blocking=False)
        if sigma is not None:
            if sigma.shape != depth.shape:
                raise ValueError(f"sigma has shape {sigma.shape}, expected {depth.shape}")
            cl.enqueue_copy(q, sbuf, hold[1], is_blocking=False)
        cl.enqueue_copy(q, self._slot_buf, hold[2], is_blocking=False)
        cl.enqueue_copy(q, self._key_buf, hold[3], is_blocking=False)
        K = self.K
        b = self._bufs
        n = m * NVOX
        gs = (n + self._wg - 1) // self._wg * self._wg
        self._kern(q, (gs,), (self._wg,), b["tsdf"], b["wsum"], b["m2"], b["s2"], b["nobs"], b["nlook"], b["luv"],
                   self._slot_buf, self._key_buf, m, dbuf, sbuf, int(sigma is not None), w, h,
                   K[0, 0], K[1, 1], K[0, 2], K[1, 2], K[0, 1], *R_cw.ravel(), *t_cw,
                   self.voxel_mm, self.trunc_mm, self.depth_min_mm, self.depth_max_mm, self.look_px ** 2)
        q.finish()  # the host arrays above must outlive the copies
        del hold

    # ------------------------------------------------------------------ results
    def download(self) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """``(keys (M,3) int32, {channel: (M, 8, 8, 8)})`` of the allocated bricks, indexed ``[z, y, x]``."""
        n = self.n_bricks
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
        sig = voxel_sigma(ch["wsum"], ch["m2"], ch["s2"], ch["nobs"], ch["nlook"])
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
        vertex with ``with_sigma``."""
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
        return V, F, N, (mesh.vertex.colors.cpu().numpy()[:, 0] * np.float32(255.0)).astype(np.float32)

    def extract_points(self, weight_threshold: float | None = None, with_sigma: bool = False) -> tuple:
        """Zero-crossing points ``(P (N,3) mm, N (N,3))``, plus ``sigma`` (N,) mm with ``with_sigma``."""
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
        self._release_storage()
        if self.dev is not None and self._img_bufs is not None:
            for bb in self._img_bufs[1:]:
                bb.release()
            self._img_bufs = None
        for bb in (getattr(self, "_slot_buf", None), getattr(self, "_key_buf", None)):
            if bb is not None:
                bb.release()
        self._slot_buf = self._key_buf = None
