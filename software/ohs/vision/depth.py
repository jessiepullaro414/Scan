"""Dense stereo depth on the projected random-dot pattern (OpenCV StereoSGBM, or census SGM on the GPU).

Input: a rectified stereo pair (grayscale ``uint8``, frame C, see :mod:`ohs.vision.rectify`).
Output: a ``float32`` depth map in mm along the optical axis of the rectified left camera, with
``0`` marking invalid pixels.

Backends
--------
``DepthParams.backend`` chooses where the matching runs:

* ``"auto"`` (default): the GPU when :func:`ohs.compute.get_device` finds one (config
  ``compute.device``, environment ``OHS_COMPUTE``), else the CPU;
* ``"cpu"``: OpenCV's SGBM, described below;
* ``"gpu"``: the GPU, or the CPU with a logged warning when there is none.

The GPU backend (:mod:`ohs.vision.depth_gpu`) is a census semi-global matcher in OpenCL with the
same contract: same search range, validity rules, left strip, uniqueness, left-right check,
3x3 median, speckle filter, sub-pixel refinement and depth conversion. Its matching cost differs
(census instead of SGBM's block cost) and it aggregates 4 paths instead of 3; on simulated frames
it is more accurate than the CPU path (fewer outliers, no depth bias) and drops depth where no
dot falls. ``wls=True`` always runs on the CPU. :attr:`DepthEstimator.backend` and
:attr:`DepthEstimator.device` say what was chosen; if the GPU fails at run time, the estimator
logs a warning and continues on the CPU.

CPU pipeline
------------
1. Optional local contrast normalisation (LCN, ``normalize=True``): ``(I - mean) / sqrt(var + eps^2)``
   over a box window, re-quantised to ``uint8``. It is off by default because SGBM's own
   x-Sobel prefilter, clipped at ``pre_filter_cap``, already copes with dot-brightness falloff and
   with the bright laser lines. On sparse dots LCN tends to turn sensor noise into false texture.
2. Left padding (``pad_left=True``): SGBM marks the leftmost ``minDisparity + numDisparities``
   columns invalid. Padding both images on the left by ``numDisparities`` (replicated border)
   recovers that strip, 5-15% of the stereo overlap, for roughly 15% more SGBM time. Pixels
   whose match window would fall outside the right image (``u - d < block_size // 2``) are
   always invalidated, so the padding never produces depth where no correspondence exists.
3. StereoSGBM (``MODE_SGBM_3WAY`` by default: multi-threaded and about 4x faster than
   ``MODE_SGBM``/``HH4`` at the same accuracy on dot images). P1/P2 follow OpenCV's
   recommendation ``8*bs^2 / 32*bs^2``. Bad matches are rejected by the uniqueness ratio, the
   left-right consistency check (``disp12MaxDiff``) and speckle filtering.
4. Optional ``cv2.ximgproc`` WLS filtering (``wls=True``). It needs a second (right-view) SGBM
   pass. Pixels whose WLS confidence is below ``wls_min_confidence`` are invalidated, so the
   filter never fills holes with invented depth.
5. Optional sub-pixel refinement (``subpixel_refine=True``, default). SGBM's parabolic sub-pixel
   interpolation is strongly *pixel-locked*: disparities cluster at integers, with errors up to
   about 0.25 px that show up as depth terraces on slanted surfaces. One Gauss-Newton step of a dense,
   gain-invariant 1-D Lucas-Kanade is applied: both images are locally normalised (zero mean, unit
   variance), the right image is warped by the SGBM disparity, and the per-pixel update
   ``-sum(g*e)/sum(g^2)`` (symmetric gradient ``g``, residual ``e``) is accumulated over a Gaussian
   window. Each pixel's residual is taken at its *own* disparity, so the window averages
   corrections rather than disparities, and depth edges are not blurred. Updates larger than
   ``refine_max_step`` px, or in textureless windows, are discarded. On synthetic dot images this
   reduces the median disparity error by 30-60% and is robust to a 30% left/right gain mismatch.
6. ``z = f * B / d``. Depths outside ``[min_depth_mm, max_depth_mm]`` are set to 0.

Search range
------------
``d = f * B / z`` with ``f = K[0, 0]`` (pixels at the processed resolution) and ``B`` in mm, so
``max_depth_mm`` sets ``minDisparity`` and ``min_depth_mm`` sets the upper end. Both ends get
``disparity_margin`` px of slack so that the sub-pixel fit at the range limits stays valid.
``numDisparities`` is rounded up to a multiple of 16, and the rounding slack extends the far end
(see :func:`disparity_range`). At 1280x800 (f ~720, B ~60) the default 150..450 mm is 96..288 px
(``minDisparity=83, numDisparities=208``). At 640x400 it is 48..144 px (``35, 112``). Surfaces far
beyond the range (a wall at 1 m, say) can still yield a few percent of random false matches;
``uniqueness_ratio`` 12-15 reduces them at a small cost in coverage.

Pixel quantities (block size, speckle window) are in pixels *of the images passed in*: the defaults
suit the half-resolution live pipeline. Full resolution (sparser dots per pixel) gives cleaner
results with ``block_size=7`` and ``speckle_window_size`` around 200.

Assumptions: rectification with ``CALIB_ZERO_DISPARITY`` (equal principal points, as produced by
``StereoCalibration.build``), so disparity is ``u_left - u_right``. One estimator per thread,
because OpenCV matchers keep internal buffers (and the GPU matcher its command queue).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import cv2
import numpy as np

__all__ = ["DepthParams", "DepthEstimator", "disparity_range", "BACKENDS"]

log = logging.getLogger(__name__)

BACKENDS = ("auto", "cpu", "gpu")
_GPU_MIN_SIZE = 32  # px (ohs.vision.depth_gpu.MIN_SIZE): smaller images are matched on the CPU

_MODES = {
    "sgbm3way": cv2.STEREO_SGBM_MODE_SGBM_3WAY,
    "sgbm": cv2.STEREO_SGBM_MODE_SGBM,
    "hh4": cv2.STEREO_SGBM_MODE_HH4,
    "hh": cv2.STEREO_SGBM_MODE_HH,
}


@dataclass
class DepthParams:
    """Parameters for :class:`DepthEstimator` (pixel quantities refer to the processed images)."""

    min_depth_mm: float = 150.0
    max_depth_mm: float = 450.0
    block_size: int = 5
    mode: str = "sgbm3way"  # "sgbm3way" | "sgbm" | "hh4" | "hh"
    p1: int | None = None  # None -> 8 * block_size**2
    p2: int | None = None  # None -> 32 * block_size**2
    uniqueness_ratio: int = 10
    disp12_max_diff: int = 1  # left-right consistency tolerance (px); <= 0 disables
    pre_filter_cap: int = 63
    speckle_window_size: int = 100  # px; 0 disables speckle filtering
    speckle_range: int = 2  # px (OpenCV multiplies by 16 internally)
    disparity_margin: int = 2  # extra search range (px) beyond the depth limits on both ends
    pad_left: bool = True
    # local contrast normalisation before matching
    normalize: bool = False
    normalize_radius: int = 7  # box half-width (px) of the local mean/variance window
    normalize_eps: float = 16.0  # DN; keeps sensor noise in dark areas from being amplified
    # sub-pixel refinement (gain-invariant 1-D Lucas-Kanade)
    subpixel_refine: bool = True
    refine_window_sigma: float = 2.3  # Gaussian accumulation window (px)
    refine_norm_radius: int = 3  # box radius (px) of the local mean/variance normalisation
    refine_max_step: float = 1.0  # px; larger updates are rejected
    # optional ximgproc WLS filter
    wls: bool = False
    wls_lambda: float = 8000.0
    wls_sigma_color: float = 1.5
    wls_min_confidence: float = 64.0  # 0..255, pixels below are invalidated
    # where the matching runs: "auto" (the GPU if there is one), "cpu" or "gpu" (module docstring)
    backend: str = "auto"

    def __post_init__(self) -> None:
        if not (0 < self.min_depth_mm < self.max_depth_mm):
            raise ValueError("need 0 < min_depth_mm < max_depth_mm")
        if self.block_size < 1 or self.block_size % 2 == 0:
            raise ValueError("block_size must be a positive odd integer")
        if self.mode not in _MODES:
            raise ValueError(f"mode must be one of {sorted(_MODES)}")
        if self.backend not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}, got {self.backend!r}")


def disparity_range(f_px: float, baseline_mm: float, min_depth_mm: float, max_depth_mm: float, margin: int = 2) -> tuple[int, int]:
    """``(minDisparity, numDisparities)`` covering depths ``[min_depth_mm, max_depth_mm]``.

    ``margin`` px of slack is added on both ends and ``numDisparities`` is rounded up to a multiple
    of 16. The rounding slack goes to the *far* end (lower ``minDisparity``), at no extra cost:
    surfaces slightly beyond ``max_depth_mm`` are then matched correctly and clipped by the depth
    range, instead of being forced onto a wrong in-range disparity.
    """
    fb = float(f_px) * float(baseline_mm)
    if fb <= 0:
        raise ValueError("focal length and baseline must be positive")
    d_lo = fb / float(max_depth_mm) if math.isfinite(max_depth_mm) else 0.0
    d_hi = fb / float(min_depth_mm)
    lo = max(0, int(math.floor(d_lo)) - int(margin))
    hi = int(math.ceil(d_hi)) + int(margin)
    num_d = max(16, int(math.ceil((hi - lo + 1) / 16.0)) * 16)
    min_d = max(0, hi - num_d + 1)
    return min_d, num_d


class _LKRefiner:
    """Dense gain-invariant 1-D Lucas-Kanade refinement of a disparity map (one Gauss-Newton step).

    All intermediate images live in buffers preallocated per image shape: at 1280x800, allocating
    fresh 4 MB arrays costs more than the arithmetic itself.
    """

    _BUFFERS = ("x", "xb", "mu", "sq", "ln", "rn", "mx", "rw", "g", "g2", "e", "num", "den", "t")
    _EPS2 = 16.0  # (4 DN)^2 noise floor of the local normalisation
    _MIN_TEXTURE = 0.02  # minimum windowed mean of g^2 (normalised units)

    def __init__(self, params: DepthParams) -> None:
        self.p = params
        self._shape: tuple[int, int] | None = None
        self._buf: dict[str, np.ndarray] = {}
        self._uu = self._vv = np.empty((0, 0), np.float32)
        s = float(params.refine_window_sigma)
        self._win_k = cv2.getGaussianKernel(int(2 * math.ceil(2.5 * s) + 1), s).astype(np.float32)
        self._pre_k = cv2.getGaussianKernel(5, 1.0).astype(np.float32)
        self._box = 2 * int(params.refine_norm_radius) + 1

    def _alloc(self, shape: tuple[int, int]) -> None:
        if self._shape == shape:
            return
        h, w = shape
        self._buf = {k: np.empty((h, w), np.float32) for k in self._BUFFERS}
        self._uu = np.tile(np.arange(w, dtype=np.float32), (h, 1))
        self._vv = np.tile(np.arange(h, dtype=np.float32)[:, None], (1, w))
        self._shape = shape

    def _normalize(self, img: np.ndarray, out: np.ndarray) -> np.ndarray:
        """``(x - mean) / sqrt(var + eps^2)`` over a box window, after a sigma=1 pre-blur."""
        b, k = self._buf, (self._box, self._box)
        np.copyto(b["x"], img, casting="unsafe")
        cv2.sepFilter2D(b["x"], -1, self._pre_k, self._pre_k, dst=b["xb"])
        cv2.blur(b["xb"], k, dst=b["mu"])
        np.multiply(b["xb"], b["xb"], out=b["t"])
        cv2.blur(b["t"], k, dst=b["sq"])
        np.multiply(b["mu"], b["mu"], out=b["t"])
        np.subtract(b["sq"], b["t"], out=b["sq"])
        np.maximum(b["sq"], 0.0, out=b["sq"])
        b["sq"] += self._EPS2
        np.sqrt(b["sq"], out=b["sq"])
        np.subtract(b["xb"], b["mu"], out=out)
        np.divide(out, b["sq"], out=out)
        return out

    def refine(self, left: np.ndarray, right: np.ndarray, disp: np.ndarray, valid: np.ndarray) -> np.ndarray:
        """Return refined disparities (new float32 array); invalid pixels are passed through."""
        self._alloc(disp.shape)
        b = self._buf
        ln = self._normalize(left, b["ln"])
        rn = self._normalize(right, b["rn"])
        validf = b["t"]
        np.copyto(validf, valid, casting="unsafe")
        # warp the right image by the current disparity (invalid pixels -> d = 0, masked below)
        np.multiply(disp, validf, out=b["mx"])
        np.subtract(self._uu, b["mx"], out=b["mx"])
        cv2.remap(rn, b["mx"], self._vv, cv2.INTER_LINEAR, dst=b["rw"], borderMode=cv2.BORDER_REPLICATE)
        # symmetric gradient g = (dL/dx + dRw/dx) / 2, residual e = L - Rw
        cv2.Sobel(ln, cv2.CV_32F, 1, 0, dst=b["g"], ksize=1, scale=0.25)
        cv2.Sobel(b["rw"], cv2.CV_32F, 1, 0, dst=b["g2"], ksize=1, scale=0.25)
        np.add(b["g"], b["g2"], out=b["g"])
        np.multiply(b["g"], validf, out=b["g"])
        np.subtract(ln, b["rw"], out=b["e"])
        np.multiply(b["g"], b["e"], out=b["e"])
        cv2.sepFilter2D(b["e"], -1, self._win_k, self._win_k, dst=b["num"])
        np.multiply(b["g"], b["g"], out=b["g"])
        cv2.sepFilter2D(b["g"], -1, self._win_k, self._win_k, dst=b["den"])
        # L(x) ~ Rw(x) - g*delta  =>  delta = -sum(g e)/sum(g^2);  d_new = d + delta
        np.maximum(b["den"], 1e-6, out=b["t"])
        np.divide(b["num"], b["t"], out=b["num"])
        ok = np.abs(b["num"]) < float(self.p.refine_max_step)
        ok &= b["den"] > self._MIN_TEXTURE
        ok &= valid
        out = disp.copy()
        np.subtract(disp, b["num"], out=out, where=ok)
        return out


class DepthEstimator:
    """Rectified stereo pair -> depth map (mm, float32, 0 = invalid).

    Parameters
    ----------
    K:
        Rectified intrinsics at the processed resolution (``Rectifier.K``).
    baseline_mm:
        Rectified baseline in mm (``Rectifier.baseline``).
    params:
        :class:`DepthParams`; defaults suit 640x400 live processing.

    Attributes
    ----------
    backend:
        ``"gpu"`` or ``"cpu"``: where the matching runs (``DepthParams.backend``, module docstring).
    device:
        The GPU's name, or ``None`` on the CPU.
    backend_note:
        Plain-language reason for the choice (e.g. why it is the CPU).
    """

    def __init__(self, K: np.ndarray, baseline_mm: float, params: DepthParams | None = None) -> None:
        self.params = params if params is not None else DepthParams()
        p = self.params
        self.K = np.asarray(K, dtype=np.float64).reshape(3, 3)
        self.f = float(self.K[0, 0])
        self.baseline = float(baseline_mm)
        if self.f <= 0 or self.baseline <= 0:
            raise ValueError("focal length and baseline must be positive")
        self.min_disparity, self.num_disparities = disparity_range(
            self.f, self.baseline, p.min_depth_mm, p.max_depth_mm, p.disparity_margin
        )
        bs = int(p.block_size)
        p1 = int(p.p1) if p.p1 is not None else 8 * bs * bs
        p2 = int(p.p2) if p.p2 is not None else 32 * bs * bs
        if p2 <= p1:
            raise ValueError("SGBM requires P2 > P1")
        self._sgbm_args = (p1, p2)
        self._sgbm = None
        self._right_matcher = None
        self._wls = None
        self._refiner: _LKRefiner | None = None
        self._cols: np.ndarray | None = None
        self.backend = "cpu"
        self.device: str | None = None
        self.backend_note = "DepthParams.backend = \"cpu\""
        self._gpu = None
        if p.backend != "cpu":
            self._gpu, self.backend_note = self._start_gpu()
            if self._gpu is not None:
                self.backend, self.device = "gpu", self._gpu.device_name
            elif p.backend == "gpu":
                log.warning("depth: DepthParams.backend is \"gpu\" but %s; using the CPU", self.backend_note)
            else:
                log.debug("depth on the CPU: %s", self.backend_note)
        if self._gpu is None:
            self._init_cpu()
        log.debug(
            "DepthEstimator: f=%.2f px B=%.2f mm depth %.0f..%.0f mm -> minDisparity=%d numDisparities=%d (%s, mode %s, bs %d)",
            self.f, self.baseline, p.min_depth_mm, p.max_depth_mm, self.min_disparity, self.num_disparities,
            f"GPU {self.device}" if self._gpu is not None else "CPU", p.mode, bs,
        )

    def _start_gpu(self) -> tuple[object | None, str]:
        """The GPU matcher and why, or ``(None, reason)``. Never raises."""
        try:
            from ohs import compute
            from ohs.vision import depth_gpu
        except ImportError as e:  # pragma: no cover - part of the package
            return None, f"the GPU code could not be loaded ({e})"
        reason = depth_gpu.gpu_unsupported(self.params)
        if reason:
            return None, reason
        dev = compute.get_device()
        if dev is None:
            return None, compute.status()
        try:
            m = depth_gpu.GpuStereoMatcher(dev, self.K, self.baseline, self.params, self.min_disparity,
                                           self.num_disparities)
        except Exception as e:  # unsupported range or device limits: the CPU path still works
            return None, f"the GPU matcher cannot run here ({e})"
        return m, f"GPU: {m.device_name}"

    def _gpu_failed(self, exc: BaseException, quiet: bool = False) -> None:
        (log.debug if quiet else log.warning)("depth: the GPU matcher failed on %s (%s); continuing on the CPU",
                                              self.device, exc)
        self._gpu = None
        self.backend, self.device = "cpu", None
        self.backend_note = f"the GPU matcher failed ({exc})"
        self._init_cpu()

    def _init_cpu(self) -> None:
        if self._sgbm is not None:
            return
        p = self.params
        p1, p2 = self._sgbm_args
        bs = int(p.block_size)
        self._sgbm = cv2.StereoSGBM_create(
            minDisparity=self.min_disparity,
            numDisparities=self.num_disparities,
            blockSize=bs,
            P1=p1,
            P2=p2,
            disp12MaxDiff=int(p.disp12_max_diff),
            preFilterCap=int(p.pre_filter_cap),
            uniquenessRatio=int(p.uniqueness_ratio),
            speckleWindowSize=int(p.speckle_window_size),
            speckleRange=int(p.speckle_range),
            mode=_MODES[p.mode],
        )
        self._right_matcher = None
        self._wls = None
        if p.wls:
            ximgproc = getattr(cv2, "ximgproc", None)
            if ximgproc is None:
                raise RuntimeError("DepthParams.wls needs opencv-contrib (cv2.ximgproc)")
            self._right_matcher = ximgproc.createRightMatcher(self._sgbm)
            self._wls = ximgproc.createDisparityWLSFilter(self._sgbm)
            self._wls.setLambda(float(p.wls_lambda))
            self._wls.setSigmaColor(float(p.wls_sigma_color))
        self._refiner = _LKRefiner(p) if p.subpixel_refine else None

    # ------------------------------------------------------------------ helpers
    @property
    def max_disparity(self) -> int:
        """Largest disparity searched (px)."""
        return self.min_disparity + self.num_disparities - 1

    def _lcn(self, img: np.ndarray) -> np.ndarray:
        p = self.params
        k = (2 * int(p.normalize_radius) + 1,) * 2
        x = np.float32(img)
        mu = cv2.blur(x, k)
        sq = cv2.blur(cv2.multiply(x, x), k)
        var = cv2.max(cv2.subtract(sq, cv2.multiply(mu, mu)), 0.0)
        y = cv2.divide(cv2.subtract(x, mu), cv2.sqrt(cv2.add(var, float(p.normalize_eps) ** 2)))
        # 128 + 32 * y, saturated to uint8 (about +-4 normalised units of range)
        return cv2.addWeighted(y, 32.0, y, 0.0, 128.0, dtype=cv2.CV_8U)

    @staticmethod
    def _check_pair(left: np.ndarray, right: np.ndarray) -> None:
        if left.ndim != 2 or right.ndim != 2 or left.dtype != np.uint8 or right.dtype != np.uint8:
            raise ValueError("expected grayscale uint8 rectified images")
        if left.shape != right.shape:
            raise ValueError(f"left/right shapes differ: {left.shape} vs {right.shape}")

    def _match(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """Raw SGBM (optionally WLS) disparity, int16 fixed point (x16), at the input size."""
        p = self.params
        pad = self.num_disparities if p.pad_left else 0
        if pad:
            lp = cv2.copyMakeBorder(left, 0, 0, pad, 0, cv2.BORDER_REPLICATE)
            rp = cv2.copyMakeBorder(right, 0, 0, pad, 0, cv2.BORDER_REPLICATE)
        else:
            lp, rp = left, right
        disp = self._sgbm.compute(lp, rp)
        if self._wls is not None:
            disp_r = self._right_matcher.compute(rp, lp)
            disp = self._wls.filter(disp, lp, None, disp_r)
            conf = self._wls.getConfidenceMap()
            invalid = np.int16((self.min_disparity - 1) * 16)  # OpenCV's own "invalid" code
            disp = np.where(conf >= p.wls_min_confidence, disp, invalid).astype(np.int16)
        return disp[:, pad:] if pad else disp

    # ------------------------------------------------------------------ public API
    def disparity(self, left_r: np.ndarray, right_r: np.ndarray) -> np.ndarray:
        """Disparity map ``u_left - u_right`` in pixels (float32, NaN = invalid)."""
        self._check_pair(left_r, right_r)
        p = self.params
        if p.normalize:
            ml, mr = self._lcn(left_r), self._lcn(right_r)
        else:
            ml, mr = left_r, right_r
        if self._gpu is not None and min(left_r.shape) < _GPU_MIN_SIZE:
            self._gpu_failed(ValueError(f"{left_r.shape[1]}x{left_r.shape[0]} images are too small for it"), quiet=True)
        if self._gpu is not None:
            try:
                return self._gpu.disparity(ml, mr, left_r, right_r)
            except Exception as e:  # out of device memory, lost device, driver error: keep going on the CPU
                self._gpu_failed(e)
        raw = self._match(ml, mr)
        valid = (raw >= self.min_disparity * 16) & (raw <= self.max_disparity * 16)
        disp = raw.astype(np.float32) * np.float32(1.0 / 16.0)
        # The match window must lie inside the right image: with left padding, SGBM can
        # otherwise propagate disparities into the left strip that has no correspondence.
        half = np.float32(self.params.block_size // 2)
        valid &= (self._columns(disp.shape[1]) - disp) >= half
        if self._refiner is not None and valid.any():
            disp = self._refiner.refine(left_r, right_r, disp, valid)
        disp[~valid] = np.nan
        return disp

    def _columns(self, width: int) -> np.ndarray:
        if self._cols is None or self._cols.shape[1] != width:
            self._cols = np.arange(width, dtype=np.float32)[None, :]
        return self._cols

    def depth_from_disparity(self, disp: np.ndarray) -> np.ndarray:
        """Convert a disparity map (px, NaN/<=0 invalid) to depth in mm (float32, 0 = invalid)."""
        p = self.params
        d = np.asarray(disp, dtype=np.float32)
        with np.errstate(divide="ignore", invalid="ignore"):
            z = np.float32(self.f * self.baseline) / d
        ok = np.isfinite(z) & (z >= p.min_depth_mm) & (z <= p.max_depth_mm)
        return np.where(ok, z, np.float32(0.0)).astype(np.float32)

    def compute(self, left_r: np.ndarray, right_r: np.ndarray) -> np.ndarray:
        """Depth map in mm (float32, same size as the inputs, 0 = invalid)."""
        if self._gpu is not None and left_r.ndim == 2 and min(left_r.shape) < _GPU_MIN_SIZE:
            self._gpu_failed(ValueError(f"{left_r.shape[1]}x{left_r.shape[0]} images are too small for it"), quiet=True)
        if self._gpu is not None:
            self._check_pair(left_r, right_r)
            p = self.params
            ml, mr = (self._lcn(left_r), self._lcn(right_r)) if p.normalize else (left_r, right_r)
            try:
                depth = self._gpu.depth(ml, mr, left_r, right_r)
            except Exception as e:
                self._gpu_failed(e)
            else:
                if depth is not None:  # None: this GPU cannot divide exactly, convert on the CPU
                    return depth
        return self.depth_from_disparity(self.disparity(left_r, right_r))
