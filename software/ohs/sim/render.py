"""Renderer for the simulated scanner: raw stereo images (uint8) and true rectified-left depth.

Image model per pixel (Lambertian surfaces; irradiance ``E`` in DN for an albedo-1 surface)::

    I = gain * V * blur( albedo * (E_amb + S * (E_dots + E_lines)) ) + black + noise

* ``E_amb`` - soft ambient light: ``ambient * (0.6 + 0.4 * max(0, n . L))`` with a fixed key
  direction ``L`` in the sim world (Z up); no shadows.
* ``E_dots`` - the DOE dot projector as a **pinhole projector** with a fixed, seeded random-dot
  image (stratified dots in the 80 x 64.4 deg field, per-dot power spread, brighter zero order):
  ``dot_power * img(a, b) * (r_ref / r)^2 * cos(theta_inc)``.
* ``E_lines`` - each laser sheet is Gaussian in the distance to its plane with
  ``sigma(r) = line_sigma_mm + line_sigma_growth * (r - r_ref)``, limited to the fan (soft edges,
  slightly brighter centre); peak ``line_power * (r_ref sigma_ref) / (r sigma)`` (power spread over
  fan length and thickness), times ``cos(theta_inc)``.
* Every emitter term is multiplied by its **visibility**: a second (shadow) ray cast from the
  surface point to the emitter origin.
* ``S`` - laser speckle, a multiplicative random field (grain ~1 px) on the coherent light only.
* Dots, lines and textures are pre-filtered by the pixel footprint (the pixel box), so single
  rays per pixel approximate area sampling; ``blur`` is the lens PSF: Gaussian ``blur_sigma`` at
  the focus distance (250 mm), widened with defocus ``|1/z - 1/focus|`` (M12 lens at ~f/2).
* ``V`` - vignetting (relative illumination ``1 - vignetting * r^2 / r_corner^2`` of the raw lens);
  ``gain`` - the right sensor is 3 % less sensitive than the left by default.
* Noise: read noise ``noise_sigma`` + shot noise (variance ``shot_noise * I``), quantised to 8
  bit, saturating at 255. Misses (no geometry) show ``background``.

``dot_power`` / ``line_power`` are the **peak DN of an un-blurred dot / line on a white (albedo 1)
surface facing the emitter at r_ref = 250 mm**. After pixel box and lens blur at 1280x800 the line
peak is ~0.87 and the dot peak ~0.65 of that (less when a dot falls between pixels), and off-axis
dots are further away and more oblique. With the defaults, a plane at 250 mm with albedo 0.7 shows
dot peaks of ~80-190 DN above ambient (10-90 % range) and lines saturating at 255.

Geometry: raw pixels are rendered through the true distortion model. For each RAW pixel (sample)
the undistorted direction ``(x, y, 1)`` (OpenCV radial-tangential model inverted to ~1e-12) is
rotated into frame C with ``R1`` (left) or ``R1 @ R.T`` (right); the right camera centre in C is
``R1 @ (-R.T @ T)`` = ``(baseline, 0, 0)``. Feeding the raw images through
``cv2.initUndistortRectifyMap(K, D, R1/R2, P1/P2)`` + ``remap`` (what ``vision.rectify`` does)
therefore yields the ideal rectified pinhole images. ``render(T_wc, rectified=True)`` renders
the rectified images directly. The returned depth is the z coordinate in frame C (rectified left
camera, ``K_rect``) of the first hit through each pixel centre, 0 where nothing is hit.
"""
from __future__ import annotations

import logging
import math
import os
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

import cv2
import numpy as np

from ohs.sim.nominal import nominal_emitters
from ohs.sim.scene import Scene, _sample
from ohs.types import ScannerCalibration, StereoCalibration

__all__ = [
    "RenderParams",
    "Renderer",
    "distort_normalized",
    "undistort_normalized",
    "raw_pixel_rays",
    "project_to_raw",
]

log = logging.getLogger(__name__)

_AMBIENT_DIR = np.array([0.3, -0.25, 1.0]) / np.linalg.norm([0.3, -0.25, 1.0])
_PIXEL_BOX_STD = 0.2887  # std of a 1-pixel box filter (1/sqrt(12))
_SHADOW_OFFSET = 1e-3  # mm, shadow-ray start offset along the normal


@dataclass
class RenderParams:
    """Rendering / sensor parameters (DN = 8-bit digital numbers)."""

    ambient: float = 40.0  # DN on a white surface facing the key direction
    ambient_on: bool = True
    dots_on: bool = True
    lines_on: bool = True
    line_names: tuple[str, ...] = ("vertical", "horizontal")
    dot_count: int | None = None  # None -> emitter's n_dots (10k)
    dot_fwhm_mrad: float = 3.0  # angular dot size: ~2.2 px FWHM at fx=720 (~2.7 px after blur)
    dot_power: float = 480.0  # peak DN, un-blurred dot, albedo 1, normal incidence, 250 mm
    dot_power_spread: float = 0.2  # per-dot power uniform in [1 - s, 1 + s]
    dot_zero_order: float = 2.0  # relative power of the central zero-order dot
    dot_seed: int = 1234  # the DOE pattern (fixed per device, independent of `seed`)
    line_power: float = 400.0  # peak DN, un-blurred line, albedo 1, normal incidence, 250 mm
    line_sigma_mm: float = 0.4  # Gaussian sigma of the sheet at ref_distance
    line_sigma_growth: float = 1.0e-3  # mm of sigma per mm of range
    fan_deg: float | None = None  # None -> emitters' fan_deg (60)
    fan_edge_deg: float = 2.0  # soft roll-off width at the fan ends
    ref_distance_mm: float = 250.0
    noise_sigma: float = 2.0  # read noise, DN
    shot_noise: float = 0.02  # shot-noise variance per DN of signal
    black_level: float = 2.0
    blur_sigma: float = 0.6  # lens PSF at best focus, px
    focus_mm: float = 250.0  # lenses are focused here (README assembly step 6)
    defocus_px_per_diopter: float = 0.2  # extra PSF sigma (px at 1280 wide) per 1/m of defocus
    speckle: float = 0.15  # laser speckle contrast (0 = off)
    vignetting: float = 0.2  # relative illumination loss at the raw image corners
    gain_right: float = 0.97  # right-camera gain relative to the left (sensor mismatch)
    background: float = 0.0  # DN where no geometry is hit
    supersample: int = 1  # rays per pixel along each axis
    seed: int = 0  # sensor noise / speckle
    threads: int = 0  # 0 -> min(8, cpu_count)
    chunk_rays: int = 1 << 17


@dataclass
class _Camera:
    name: str
    size: tuple[int, int]  # final (w, h)
    ss: int
    dirs: np.ndarray  # (N,3) float64 unit directions in C, row-major over the (h*ss, w*ss) grid
    origin: np.ndarray  # (3,) camera centre in C
    pix_angle: np.ndarray  # (N,) angular sample pitch (rad), for footprints
    vignette: np.ndarray  # (h, w) float32
    is_rect_left: bool  # identical to the rectified-left pixel-centre grid


@dataclass
class _Projector:
    origin: np.ndarray
    R: np.ndarray  # projector axes -> C
    tan_x: float
    tan_y: float
    pitch: float
    pad: int
    image: np.ndarray  # float32 relative dot irradiance


@dataclass
class _Laser:
    name: str
    origin: np.ndarray
    normal: np.ndarray
    d: float
    beam: np.ndarray
    fan_axis: np.ndarray
    half_fan: float


# --------------------------------------------------------------------------- lens model
def _dist_coeffs(D: np.ndarray) -> np.ndarray:
    D = np.asarray(D, dtype=np.float64).ravel()
    if D.size > 8 and np.any(D[8:] != 0):
        raise ValueError("thin-prism / tilted distortion terms are not supported by the simulator")
    out = np.zeros(8)
    out[: min(8, D.size)] = D[:8]
    return out


def distort_normalized(xy: np.ndarray, D: np.ndarray) -> np.ndarray:
    """OpenCV radial-tangential (+ rational) distortion of normalised coordinates (N,2)."""
    k1, k2, p1, p2, k3, k4, k5, k6 = _dist_coeffs(D)
    x, y = xy[:, 0], xy[:, 1]
    r2 = x * x + y * y
    radial = (1 + r2 * (k1 + r2 * (k2 + r2 * k3))) / (1 + r2 * (k4 + r2 * (k5 + r2 * k6)))
    xd = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
    yd = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
    return np.stack([xd, yd], axis=1)


def undistort_normalized(xy_d: np.ndarray, D: np.ndarray, iters: int = 6, tol: float = 1e-14) -> np.ndarray:
    """Invert :func:`distort_normalized` by Newton iteration with the analytic Jacobian,
    starting from the distorted point (residual ~1e-15 for lens-like distortion)."""
    xy_d = np.asarray(xy_d, dtype=np.float64).reshape(-1, 2)
    c = _dist_coeffs(D)
    if not np.any(c):
        return xy_d.copy()
    k1, k2, p1, p2, k3, k4, k5, k6 = c
    xt, yt = xy_d[:, 0], xy_d[:, 1]
    x, y = xt.copy(), yt.copy()
    for _ in range(iters):
        r2 = x * x + y * y
        num = 1 + r2 * (k1 + r2 * (k2 + r2 * k3))
        den = 1 + r2 * (k4 + r2 * (k5 + r2 * k6))
        rad = num / den
        drad = ((k1 + r2 * (2 * k2 + 3 * k3 * r2)) * den - num * (k4 + r2 * (2 * k5 + 3 * k6 * r2))) / (den * den)
        fx = x * rad + 2 * p1 * x * y + p2 * (r2 + 2 * x * x) - xt
        fy = y * rad + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y - yt
        xy2 = 2 * x * y * drad
        a = rad + 2 * x * x * drad + 2 * p1 * y + 6 * p2 * x
        b = xy2 + 2 * p1 * x + 2 * p2 * y
        dd = rad + 2 * y * y * drad + 6 * p1 * y + 2 * p2 * x
        det = a * dd - b * b
        dx = (dd * fx - b * fy) / det
        dy = (-b * fx + a * fy) / det
        x -= dx
        y -= dy
        if max(float(np.max(np.abs(dx))), float(np.max(np.abs(dy)))) < tol:
            break
    return np.stack([x, y], axis=1)


def raw_pixel_rays(uv: np.ndarray, K: np.ndarray, D: np.ndarray) -> np.ndarray:
    """Unit rays (N,3) in a raw camera's frame through distorted pixel positions ``uv`` (N,2)."""
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    fx, fy, cx, cy, s = K[0, 0], K[1, 1], K[0, 2], K[1, 2], K[0, 1]
    y = (uv[:, 1] - cy) / fy
    x = (uv[:, 0] - cx - s * y) / fx
    xy = undistort_normalized(np.stack([x, y], axis=1), D)
    rays = np.column_stack([xy, np.ones(len(xy))])
    return rays / np.linalg.norm(rays, axis=1, keepdims=True)


def _raw_frames(st: StereoCalibration) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """``{side: (R_c_raw, origin_c, K, D)}``: raw camera orientation/centre in frame C."""
    R1 = np.asarray(st.R1, dtype=np.float64)
    R, T = np.asarray(st.R, dtype=np.float64), np.asarray(st.T, dtype=np.float64).reshape(3)
    return {
        "left": (R1, np.zeros(3), np.asarray(st.K1, dtype=np.float64), np.asarray(st.D1, dtype=np.float64)),
        "right": (R1 @ R.T, R1 @ (-R.T @ T), np.asarray(st.K2, dtype=np.float64), np.asarray(st.D2, dtype=np.float64)),
    }


def project_to_raw(P_c: np.ndarray, stereo: StereoCalibration, side: str = "left") -> np.ndarray:
    """Project points in frame C (N,3) into the RAW (distorted) left/right image (N,2) px."""
    R_cr, o_c, K, D = _raw_frames(stereo)[side]
    P = (np.asarray(P_c, dtype=np.float64).reshape(-1, 3) - o_c) @ R_cr  # raw camera frame
    with np.errstate(divide="ignore", invalid="ignore"):
        xy = np.where(P[:, 2:3] > 0, P[:, :2] / P[:, 2:3], np.nan)
    xyd = distort_normalized(xy, D)
    u = K[0, 0] * xyd[:, 0] + K[0, 1] * xyd[:, 1] + K[0, 2]
    v = K[1, 1] * xyd[:, 1] + K[1, 2]
    return np.stack([u, v], axis=1)


# --------------------------------------------------------------------------- renderer
class Renderer:
    """Render raw stereo frames of a :class:`~ohs.sim.scene.Scene` seen by the scanner.

    ``calib`` is the TRUE calibration (``ScannerCalibration`` or ``StereoCalibration``),
    ``emitters`` a :func:`~ohs.sim.nominal.nominal_emitters` dict (default: nominal for the
    calibration's baseline). Call :meth:`close` (or use as a context manager) to release the
    worker threads early.
    """

    def __init__(
        self,
        calib: ScannerCalibration | StereoCalibration,
        emitters: dict | None = None,
        scene: Scene | None = None,
        params: RenderParams | None = None,
    ) -> None:
        if scene is None:
            raise ValueError("Renderer needs a scene")
        t0 = time.perf_counter()
        self.stereo = calib.stereo if isinstance(calib, ScannerCalibration) else calib
        self.params = params or RenderParams()
        self.emitters = emitters if emitters is not None else nominal_emitters(baseline=self.stereo.baseline)
        self.scene = scene
        self.size = (int(self.stereo.image_size[0]), int(self.stereo.image_size[1]))
        self._rng = np.random.default_rng(self.params.seed)
        self._frames = _raw_frames(self.stereo)
        self._K_rect = self.stereo.K_rect
        st = self.stereo
        self._identity = (
            not np.any(np.asarray(st.D1, dtype=np.float64))
            and np.allclose(st.R1, np.eye(3), atol=1e-12)
            and np.allclose(st.K1, self._K_rect, atol=1e-9)
        )
        ss = max(1, int(self.params.supersample))
        self._r2_corner = {side: self._corner_r2(side) for side in ("left", "right")}
        self._raw = tuple(self._make_raw_camera(side, ss) for side in ("left", "right"))
        self._rect: tuple[_Camera, _Camera] | None = None
        self._depth_cam: _Camera | None = None
        self._proj = self._build_projector(float(self.stereo.K1[0, 0]) * ss)
        self._lasers = self._build_lasers()
        n_threads = self.params.threads or min(8, os.cpu_count() or 1)
        self._pool = ThreadPoolExecutor(max_workers=n_threads, thread_name_prefix="sim-render") if n_threads > 1 else None
        if self._pool is not None:
            self._finalizer = weakref.finalize(self, self._pool.shutdown, wait=False)
        log.debug("renderer ready (%dx%d, ss=%d, projector %s) in %.2fs", *self.size, ss, self._proj.image.shape, time.perf_counter() - t0)

    # ------------------------------------------------------------------ public API
    def render(self, T_wc: np.ndarray, rectified: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Render ``(left, right, depth)`` for camera pose ``T_wc`` (frame C -> sim world).

        ``left``/``right`` are RAW uint8 images (or rectified ones with ``rectified=True``),
        ``depth`` float32 (h, w) z-depth in mm in the rectified left camera, 0 = no hit.
        """
        T_wc = np.asarray(T_wc, dtype=np.float64).reshape(4, 4)
        t0 = time.perf_counter()
        cams = self._rect_cameras() if rectified else self._raw
        jobs = [(cams[0], False, False), (cams[1], False, False)]
        reuse_depth = cams[0].is_rect_left
        if not reuse_depth:
            jobs.append((self._depth_camera(), False, True))
        res = self._trace_many(jobs, T_wc)
        left = self._finish(res[0], cams[0], 1.0)
        right = self._finish(res[1], cams[1], self.params.gain_right)
        w, h = self.size
        depth = (res[0] if reuse_depth else res[2])["z"].reshape(h, w).astype(np.float32)
        log.debug("rendered %dx%d stereo frame in %.3fs", w, h, time.perf_counter() - t0)
        return left, right, depth

    def render_aux(self, T_wc: np.ndarray) -> dict[str, np.ndarray]:
        """Ground-truth layers for the rectified left camera (pixel centres, K_rect).

        Keys: ``depth`` (h,w) mm, ``obj`` (h,w) int32 object id (-1 = miss), ``points_c``
        (h,w,3), ``normal_c`` (h,w,3) (facing the camera), ``albedo``, irradiances ``E_ambient``,
        ``E_dots``, ``E_<laser>`` (DN for albedo 1, before speckle/blur), and ``lit_dots``,
        ``lit_<laser>`` (bool: inside the emitter's field/sheet band, facing it and not shadowed).
        """
        T_wc = np.asarray(T_wc, dtype=np.float64).reshape(4, 4)
        cam = self._depth_camera()
        r = self._trace_many([(cam, True, False)], T_wc)[0]
        w, h = self.size
        out: dict[str, np.ndarray] = {}
        for k, v in r.items():
            if k in ("amb", "coh"):
                continue
            out["depth" if k == "z" else k] = v.reshape((h, w) + v.shape[1:])
        return out

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def __enter__(self) -> "Renderer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ cameras
    def _corner_r2(self, side: str) -> float:
        _, _, K, D = self._frames[side]
        w, h = self.size
        uv = np.array([[0.0, 0.0], [w - 1.0, 0.0], [0.0, h - 1.0], [w - 1.0, h - 1.0]])
        rays = raw_pixel_rays(uv, K, D)
        return float(np.max((rays[:, 0] ** 2 + rays[:, 1] ** 2) / rays[:, 2] ** 2))

    def _grid(self, ss: int) -> np.ndarray:
        w, h = self.size
        us = (np.arange(w * ss) + 0.5) / ss - 0.5
        vs = (np.arange(h * ss) + 0.5) / ss - 0.5
        U, V = np.meshgrid(us, vs)
        return np.column_stack([U.ravel(), V.ravel()])

    def _vignette(self, side: str, dirs_raw: np.ndarray, ss: int) -> np.ndarray:
        w, h = self.size
        r2 = (dirs_raw[:, 0] ** 2 + dirs_raw[:, 1] ** 2) / dirs_raw[:, 2] ** 2
        v = np.clip(1.0 - self.params.vignetting * r2 / self._r2_corner[side], 0.2, 1.0)
        v = v.reshape(h * ss, w * ss)
        if ss > 1:
            v = v.reshape(h, ss, w, ss).mean(axis=(1, 3))
        return v.astype(np.float32)

    def _make_raw_camera(self, side: str, ss: int) -> _Camera:
        R_cr, origin, K, D = self._frames[side]
        uv = self._grid(ss)
        rays = raw_pixel_rays(uv, K, D)
        dirs = rays @ R_cr.T
        pix = (rays[:, 2] ** 1.5) / (float(K[0, 0]) * ss)
        is_rl = side == "left" and ss == 1 and self._identity
        return _Camera(f"raw_{side}", self.size, ss, dirs, origin, pix, self._vignette(side, rays, ss), is_rl)

    def _make_rect_camera(self, side: str, ss: int) -> _Camera:
        R_cr, origin, _, _ = self._frames[side]
        P = self.stereo.P1 if side == "left" else self.stereo.P2
        K = np.asarray(P, dtype=np.float64)[:3, :3]
        uv = self._grid(ss)
        y = (uv[:, 1] - K[1, 2]) / K[1, 1]
        x = (uv[:, 0] - K[0, 2] - K[0, 1] * y) / K[0, 0]
        dirs = np.column_stack([x, y, np.ones_like(x)])
        dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
        rays_raw = dirs @ R_cr  # same rays in the raw camera frame (for vignetting)
        pix = (dirs[:, 2] ** 1.5) / (float(K[0, 0]) * ss)
        o = np.zeros(3) if side == "left" else origin
        return _Camera(f"rect_{side}", self.size, ss, dirs, o, pix, self._vignette(side, rays_raw, ss), side == "left" and ss == 1)

    def _rect_cameras(self) -> tuple[_Camera, _Camera]:
        if self._rect is None:
            ss = max(1, int(self.params.supersample))
            self._rect = (self._make_rect_camera("left", ss), self._make_rect_camera("right", ss))
        return self._rect

    def _depth_camera(self) -> _Camera:
        if self._depth_cam is None:
            if self._raw[0].is_rect_left:
                self._depth_cam = self._raw[0]
            elif self._rect is not None and self._rect[0].is_rect_left:
                self._depth_cam = self._rect[0]
            else:
                self._depth_cam = self._make_rect_camera("left", 1)
        return self._depth_cam

    # ------------------------------------------------------------------ emitters
    def _build_projector(self, f_eff: float) -> _Projector:
        p, em = self.params, self.emitters["dots"]
        fov_h, fov_v = em.get("fov_deg", (80.0, 64.4))
        tx, ty = math.tan(math.radians(fov_h) / 2), math.tan(math.radians(fov_v) / 2)
        n_dots = int(p.dot_count if p.dot_count is not None else em.get("n_dots", 10000))
        sig_dot = p.dot_fwhm_mrad * 1e-3 / 2.3548
        sig = math.hypot(sig_dot, _PIXEL_BOX_STD / f_eff)
        pitch = sig / 1.6
        rad = int(math.ceil(4.0 * sig / pitch))
        pad = rad + 2
        wp = int(math.ceil(2 * tx / pitch)) + 2 * pad
        hp = int(math.ceil(2 * ty / pitch)) + 2 * pad
        rng = np.random.default_rng(p.dot_seed)
        nx = max(1, int(round(math.sqrt(n_dots * tx / ty))))
        ny = max(1, int(round(n_dots / nx)))
        ii, jj = np.meshgrid(np.arange(nx), np.arange(ny))
        a = ((ii.ravel() + rng.random(ii.size)) / nx * 2.0 - 1.0) * tx
        b = ((jj.ravel() + rng.random(jj.size)) / ny * 2.0 - 1.0) * ty
        amp = 1.0 + p.dot_power_spread * (2.0 * rng.random(a.size) - 1.0)
        a, b, amp = np.append(a, 0.0), np.append(b, 0.0), np.append(amp, p.dot_zero_order)
        col = (a + tx) / pitch - 0.5 + pad
        row = (b + ty) / pitch - 0.5 + pad
        off = np.arange(-rad, rad + 1)
        ci = np.floor(col).astype(np.int64)[:, None, None] + off[None, None, :]
        ri = np.floor(row).astype(np.int64)[:, None, None] + off[None, :, None]
        d2 = ((ci - col[:, None, None]) ** 2 + (ri - row[:, None, None]) ** 2) * pitch**2
        wts = amp[:, None, None] * (sig_dot / sig) ** 2 * np.exp(-0.5 * d2 / sig**2)
        idx = np.broadcast_to(ri * wp + ci, wts.shape).ravel()
        img = np.bincount(idx, weights=wts.ravel(), minlength=hp * wp).reshape(hp, wp).astype(np.float32)
        R = np.asarray(em.get("R", np.eye(3)), dtype=np.float64)
        return _Projector(np.asarray(em["origin"], dtype=np.float64), R, tx, ty, pitch, pad, img)

    def _build_lasers(self) -> list[_Laser]:
        out = []
        for name in self.params.line_names:
            em = self.emitters.get(name)
            if em is None:
                log.warning("no emitter named %r; line skipped", name)
                continue
            fan = self.params.fan_deg if self.params.fan_deg is not None else float(em.get("fan_deg", 60.0))
            n = np.asarray(em["normal"], dtype=np.float64)
            out.append(
                _Laser(
                    name,
                    np.asarray(em["origin"], dtype=np.float64),
                    n / np.linalg.norm(n),
                    float(em["d"]) / float(np.linalg.norm(n)),
                    np.asarray(em["direction"], dtype=np.float64),
                    np.asarray(em["fan_axis"], dtype=np.float64),
                    math.radians(fan) / 2,
                )
            )
        return out

    # ------------------------------------------------------------------ tracing
    def _trace_many(self, jobs: list[tuple[_Camera, bool, bool]], T_wc: np.ndarray) -> list[dict[str, np.ndarray]]:
        cs = max(1024, int(self.params.chunk_rays))
        tasks = []
        for j, (cam, aux, depth_only) in enumerate(jobs):
            n = len(cam.dirs)
            for s in range(0, n, cs):
                tasks.append((j, cam, s, min(n, s + cs), aux, depth_only))

        def run(task: tuple) -> dict[str, np.ndarray]:
            _, cam, s, e, aux, depth_only = task
            return self._shade(cam.origin, cam.dirs[s:e], cam.pix_angle[s:e], T_wc, aux, depth_only)

        results = list(self._pool.map(run, tasks)) if self._pool is not None and len(tasks) > 1 else [run(t) for t in tasks]
        out = []
        for j in range(len(jobs)):
            parts = [r for t, r in zip(tasks, results) if t[0] == j]
            out.append({k: np.concatenate([p[k] for p in parts]) for k in parts[0]})
        return out

    def _visible(self, X_w: np.ndarray, n_w: np.ndarray, src_w: np.ndarray) -> np.ndarray:
        """Shadow rays from surface points to an emitter origin: True = unoccluded."""
        if len(X_w) == 0:
            return np.zeros(0, dtype=bool)
        start = X_w + _SHADOW_OFFSET * n_w
        v = src_w - start
        dist = np.linalg.norm(v, axis=1)
        return ~self.scene.occluded(start, v / dist[:, None], dist - _SHADOW_OFFSET)

    def _dots(
        self, X_c: np.ndarray, n_c: np.ndarray, X_w: np.ndarray, n_w: np.ndarray, T_wc: np.ndarray, aux: bool
    ) -> tuple[np.ndarray, np.ndarray]:
        """Dot-projector irradiance at hit points (C and W coordinates) and the lit mask."""
        m = len(X_c)
        E = np.zeros(m)
        lit = np.zeros(m, dtype=bool)
        pj, p = self._proj, self.params
        v = X_c - pj.origin
        vp = v @ pj.R
        z = vp[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            a, b = vp[:, 0] / z, vp[:, 1] / z
        inside = (z > 1e-6) & (np.abs(a) <= pj.tan_x) & (np.abs(b) <= pj.tan_y)
        sel = np.flatnonzero(inside)
        if sel.size == 0:
            return E, lit
        val = _sample(pj.image, (a[sel] + pj.tan_x) / pj.pitch - 0.5 + pj.pad, (b[sel] + pj.tan_y) / pj.pitch - 0.5 + pj.pad, cv2.BORDER_CONSTANT, 0.0)
        r = np.linalg.norm(v[sel], axis=1)
        cos_inc = -np.einsum("ij,ij->i", n_c[sel], v[sel]) / r
        cand = (cos_inc > 0) if aux else (cos_inc > 0) & (val > 1e-4)
        sel, val, r, cos_inc = sel[cand], val[cand], r[cand], cos_inc[cand]
        src_w = T_wc[:3, :3] @ pj.origin + T_wc[:3, 3]
        vis = self._visible(X_w[sel], n_w[sel], src_w)
        E[sel] = p.dot_power * val * (p.ref_distance_mm / r) ** 2 * cos_inc * vis
        lit[sel] = vis
        return E, lit

    def _line(
        self,
        las: _Laser,
        X_c: np.ndarray,
        n_c: np.ndarray,
        X_w: np.ndarray,
        n_w: np.ndarray,
        T_wc: np.ndarray,
        footprint: np.ndarray,
        aux: bool,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Laser-sheet irradiance at hit points and the lit mask (in-band, in-fan, unshadowed).

        ``aux`` is accepted for symmetry with :meth:`_dots`; shadow rays are only cast inside
        the +-6 sigma band either way."""
        m = len(X_c)
        E = np.zeros(m)
        lit = np.zeros(m, dtype=bool)
        p = self.params
        dist = X_c @ las.normal + las.d
        sel = np.flatnonzero(np.abs(dist) < 12.0)
        if sel.size == 0:
            return E, lit
        w = X_c[sel] - las.origin
        r = np.linalg.norm(w, axis=1)
        sig = np.maximum(p.line_sigma_mm + p.line_sigma_growth * (r - p.ref_distance_mm), 0.05)
        sig_eff = np.sqrt(sig**2 + (_PIXEL_BOX_STD * footprint[sel]) ** 2)
        q = dist[sel] / sig_eff
        along = w @ las.beam
        phi = np.arctan2(w @ las.fan_axis, along)
        cos_inc = -np.einsum("ij,ij->i", n_c[sel], w) / r
        band = (np.abs(q) < 6.0) & (along > 0) & (np.abs(phi) < las.half_fan + math.radians(p.fan_edge_deg)) & (cos_inc > 0)
        sel, q, r, sig_eff, phi, cos_inc = sel[band], q[band], r[band], sig_eff[band], phi[band], cos_inc[band]
        if sel.size == 0:
            return E, lit
        edge = math.radians(max(p.fan_edge_deg, 1e-3))
        s = np.clip(0.5 + (las.half_fan - np.abs(phi)) / edge, 0.0, 1.0)
        fan = (1.0 - 0.25 * (phi / las.half_fan) ** 2) * s * s * (3.0 - 2.0 * s)
        src_w = T_wc[:3, :3] @ las.origin + T_wc[:3, 3]
        vis = self._visible(X_w[sel], n_w[sel], src_w)
        peak = p.line_power * (p.ref_distance_mm * p.line_sigma_mm) / (r * sig_eff)
        E[sel] = peak * np.exp(-0.5 * q * q) * fan * cos_inc * vis
        lit[sel] = vis & (np.abs(q) < 3.0) & (s > 0.5)
        return E, lit

    def _shade(
        self, o_c: np.ndarray, d_c: np.ndarray, pix: np.ndarray, T_wc: np.ndarray, aux: bool, depth_only: bool
    ) -> dict[str, np.ndarray]:
        """Trace one chunk of rays (origin ``o_c``, unit directions ``d_c`` in frame C).

        Returns ``z`` (depth in C, 0 = miss) and, unless ``depth_only``, the incoherent (``amb``)
        and laser (``coh``) radiance per ray; ``aux`` adds the ground-truth layers."""
        p = self.params
        R, tw = T_wc[:3, :3], T_wc[:3, 3]
        d_w = d_c @ R.T
        o_w = R @ o_c + tw
        hit = self.scene.cast(o_w, d_w, pix, surface=not depth_only)
        n = len(d_c)
        out: dict[str, np.ndarray] = {"z": np.zeros(n, np.float32)}
        idx = np.flatnonzero(hit.obj >= 0)
        t = hit.t[idx]
        X_c = o_c + t[:, None] * d_c[idx]
        out["z"][idx] = X_c[:, 2]
        if depth_only:
            return out
        n_w = hit.normal[idx]
        alb = hit.albedo[idx]
        X_w = o_w + t[:, None] * d_w[idx]
        n_c = n_w @ R
        m = idx.size
        E_amb = p.ambient * (0.6 + 0.4 * np.clip(n_w @ _AMBIENT_DIR, 0.0, 1.0)) if p.ambient_on else np.zeros(m)
        E_coh = np.zeros(m)
        layers: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        if p.dots_on:
            layers["dots"] = self._dots(X_c, n_c, X_w, n_w, T_wc, aux)
            E_coh += layers["dots"][0]
        if p.lines_on:
            fp = t * pix[idx]
            for las in self._lasers:
                layers[las.name] = self._line(las, X_c, n_c, X_w, n_w, T_wc, fp, aux)
                E_coh += layers[las.name][0]
        amb = np.full(n, p.background, np.float32)
        coh = np.zeros(n, np.float32)
        amb[idx] = alb * E_amb
        coh[idx] = alb * E_coh
        out["amb"], out["coh"] = amb, coh
        if aux:
            full = lambda shape, dtype=np.float32: np.zeros((n,) + shape, dtype)  # noqa: E731
            out["obj"] = hit.obj.astype(np.int32)
            out["points_c"], out["normal_c"] = full((3,)), full((3,))
            out["points_c"][idx], out["normal_c"][idx] = X_c, n_c
            out["albedo"] = full(())
            out["albedo"][idx] = alb
            out["E_ambient"] = full(())
            out["E_ambient"][idx] = E_amb
            for name in ["dots"] + [las.name for las in self._lasers]:
                E_k, lit_k = layers.get(name, (np.zeros(m), np.zeros(m, dtype=bool)))
                out[f"E_{name}"] = full(())
                out[f"E_{name}"][idx] = E_k
                out[f"lit_{name}"] = full((), bool)
                out[f"lit_{name}"][idx] = lit_k
        return out

    # ------------------------------------------------------------------ sensor
    def _speckle(self, h: int, w: int) -> np.ndarray:
        g = self._rng.standard_normal((h, w), dtype=np.float32)
        g = cv2.GaussianBlur(g, (0, 0), 0.6)
        g /= max(float(g.std()), 1e-6)
        return np.maximum(1.0 + self.params.speckle * g, 0.0)

    def _blur(self, img: np.ndarray, z: np.ndarray, width: int) -> np.ndarray:
        """Lens PSF: Gaussian ``blur_sigma`` at focus, growing with defocus |1/z - 1/focus|.

        Spatially varying blur is approximated by blending 5 uniformly blurred copies per pixel
        (no occlusion-aware defocus at depth edges)."""
        p = self.params
        dio = np.where(z > 0, np.abs(1000.0 / np.maximum(z, 1.0) - 1000.0 / p.focus_mm), 0.0)
        sig = np.sqrt(p.blur_sigma**2 + (p.defocus_px_per_diopter * width / 1280.0 * dio) ** 2).astype(np.float32)
        smin, smax = float(sig.min()), float(sig.max())

        def g(s: float) -> np.ndarray:
            return cv2.GaussianBlur(img, (0, 0), s) if s > 0.05 else img

        if smax - smin < 0.02:
            return g(smax)
        levels = np.linspace(smin, smax, 5)
        stack = np.stack([g(float(s)) for s in levels])
        pos = (sig - smin) / (smax - smin) * (len(levels) - 1)
        i = np.clip(np.floor(pos).astype(np.int64), 0, len(levels) - 2)
        f = (pos - i).astype(np.float32)
        lo = np.take_along_axis(stack, i[None], axis=0)[0]
        hi = np.take_along_axis(stack, i[None] + 1, axis=0)[0]
        return lo + f * (hi - lo)

    def _finish(self, res: dict[str, np.ndarray], cam: _Camera, gain: float) -> np.ndarray:
        p = self.params
        w, h = cam.size
        ss = cam.ss
        amb = res["amb"].reshape(h * ss, w * ss)
        coh = res["coh"].reshape(h * ss, w * ss)
        z = res["z"].reshape(h * ss, w * ss)
        if ss > 1:
            amb = amb.reshape(h, ss, w, ss).mean(axis=(1, 3))
            coh = coh.reshape(h, ss, w, ss).mean(axis=(1, 3))
            z = z.reshape(h, ss, w, ss).max(axis=(1, 3))
        if p.speckle > 0 and np.any(coh):
            coh = coh * self._speckle(h, w)
        img = (amb + coh).astype(np.float32)
        if p.blur_sigma > 0 or p.defocus_px_per_diopter > 0:
            img = self._blur(img, z, w)
        img = img * cam.vignette * np.float32(gain) + np.float32(p.black_level)
        sigma = np.sqrt(p.noise_sigma**2 + p.shot_noise * np.maximum(img, 0.0))
        img = img + self._rng.standard_normal(img.shape, dtype=np.float32) * sigma
        return np.clip(np.rint(img), 0, 255).astype(np.uint8)

    def with_params(self, **changes: object) -> "Renderer":
        """A new renderer sharing calibration/emitters/scene with some parameters changed."""
        return Renderer(self.stereo, self.emitters, self.scene, replace(self.params, **changes))
