"""Per-frame processing: raw stereo frame -> rectified pair, depth, laser points, pose, fusion.

:class:`FramePipeline` is the one place where the vision, tracking and fusion modules are wired
together. Live scanning runs it at ``scale=0.5`` for feedback, and offline processing
(:mod:`ohs.app.process`) at ``scale=1.0``. Per frame (:meth:`FramePipeline.process`):

1. **Rectify** the raw pair (:class:`~ohs.vision.rectify.Rectifier` at ``scale``). From here on
   everything is in frame C at that scale (``K = rectifier.K``).
2. **Depth** (:class:`~ohs.vision.depth.DepthEstimator`, stereo matching on the dot pattern),
   computed when the mode needs it: always in markerless mode, and in marker mode when
   ``integrate_depth``. ``DepthParams.backend`` picks the matcher: GPU census SGM
   (:mod:`ohs.vision.depth_gpu`, OpenCL) or CPU SGBM; the default ``"auto"`` uses the GPU when
   :func:`ohs.compute.get_device` finds one.
3. **Laser lines**, extracted once per image (left and right) and triangulated on the calibrated
   planes with the right-image stereo check (:func:`~ohs.vision.laser.laser_points` with
   ``lines_left`` / ``lines_right``) and the check against the frame's dense depth and its sigma
   (:func:`~ohs.vision.laser.depth_check`; :attr:`FramePipeline.laser_rejected` counts the
   points it drops, per line).
4. **Pose**.

   * ``"markerless"``: :class:`~ohs.track.markerless.MarkerlessTracker` frame-to-model ICP. The
     gyro rotation since the previous processed frame
     (:class:`~ohs.track.imu_prior.GyroPredictor`) is passed as ``R_prior`` only when the IMU
     buffer covers that interval, and the frame time ``t`` is always passed. See *tracking
     model* below.
   * ``"markers"``: :class:`~ohs.track.markers.MarkerTracker` gives the pose; on success the
     depth is fused with ``MarkerlessTracker.integrate(depth, None, T_wc, t=t)``.
5. **Laser cloud**: the validated laser points are added to the
   :class:`~ohs.track.fusion.LaserCloud` with the frame pose, only when tracking succeeded.

**Tracking model** (markerless). Stereo depth carries an error pattern that is fixed to the
*camera*: the projected dots sit at nearly the same image positions in every frame, so the
dot-scale disparity errors (SGBM: std ~0.3 px in simulation, frame-to-frame correlation ~0.4) repeat.
A TSDF built from such depth keeps the pattern, and frame-to-model ICP then locks onto it: large
flat areas (a table, a panel) pull the estimate toward the previous pose. In simulation this
drifted 20 deg / 55 mm over 36 frames on the sphere scene (ground-truth depth: 0.08 deg /
0.2 mm). The pipeline therefore tracks against a *separate* tracking TSDF fed with a filtered
depth map (:func:`tracking_depth`: edge-aware normalised Gaussian smoothing of ``sigma`` ~6 px at
1280 px width, validity eroded by 2 sigma so that no camera-fixed border artefact remains),
at ``track_voxel_mm`` (2 mm), with a gyro prior of 0.02 deg per frame (the calibrated gyro is
good to ~0.01 deg per frame). The raw depth is fused at the tracked poses into the
reconstruction TSDF (``fusion``, 1 mm at full resolution), which is what :meth:`mesh` returns.
Simulated result: 0.07 deg / 0.3 mm on the same 36 frames (VALIDATION.md).
``track_filter_px=0`` restores single-model tracking on the raw depth.

The filter runs at the *tracking resolution* ``track_size`` = ``fusion.raycast_scale`` of the
processed image (640x400 at full quality, the resolution the model is ray cast at): only its
edge test runs on the full-resolution pixels; smoothing and erosion run at ``track_size`` with
sigma and radius scaled, and the smooth result is upsampled back for the tracker, which is
unchanged (integration and ICP sampling at the processed resolution). See :func:`tracking_depth`.
With a GPU the filter runs there (:class:`ohs.track.filter_gpu.TrackingFilterGpu`, the same map).

**Detailed parts.** The filter erodes around every depth step, so a detailed part (a carburetor,
a machined bracket) loses most of its depth, and what is left (often the bench) can't hold the
scanner's sideways motion. Each ICP measures how well its input holds the weakest translation
(``info["trans_strength"]``; :mod:`ohs.track.markerless`, "Detailed parts"). During the first
``DETAIL_PROBE_FRAMES`` frames of a scan a second tracker runs on :func:`with_raw_depth`, the
filtered depth plus the raw depth wherever the filter dropped a pixel. If the filtered depth can't
hold the pose (``DETAIL_PROBE_STRENGTH``), that tracker, consistent from the first frame on, takes
over for the whole scan (``tracker.detail_mode``); otherwise it is dropped. A frame that fails a
gate tries :func:`with_raw_depth` before it is lost.

Switching later, or frame by frame, was much worse on the simulator: the part goes into the model
at poses that had already slid, and the filter's leftover fragments of it look well held once it
is there but are biased (``VALIDATION.md``, "Large and detailed parts"). Smooth parts (the sphere,
the car panel, the door trim) never switch. A scan that starts on a smooth part and only later
reaches a detailed one stays on the filtered depth.

**Model backends.** The tracking model may live on the GPU
(:class:`~ohs.track.volume_gpu.GpuTsdfVolume`, a rolling cube): it takes
``fusion.model_backend`` (default ``"auto"``: the GPU when :func:`ohs.compute.get_device` finds
one); the single model of ``track_filter_px=0`` uses Open3D. :attr:`FramePipeline.model_backend`
says which backend tracks (``"gpu"`` / ``"open3d"``), :attr:`FramePipeline.model_device` where.

**Reconstruction model** (``PipelineParams.recon``; it keeps the whole scan and makes the mesh).
``"auto"`` (default) is :class:`~ohs.track.recon_tsdf.ConfidenceTsdf` on the GPU when there is
one, else Open3D's TSDF; ``"confidence"`` forces the confidence TSDF (numpy without a GPU, about
0.5 s a frame); ``"open3d"`` forces Open3D. The confidence TSDF averages like Open3D (its mesh is
Open3D's, vertex for vertex) and also keeps each voxel's uncertainty: each frame's depth comes
with its sigma (:meth:`~ohs.vision.depth.DepthEstimator.compute_with_sigma`), and
:meth:`mesh_with_sigma` gives each vertex its standard deviation in mm, which the PLY carries as
``quality``. :attr:`FramePipeline.recon_status` says which model makes the mesh, in plain words.
Weighting frames by 1/sigma^2 instead of averaging made the mesh worse (VALIDATION.md "Depth
confidence"), so the sigmas only measure.

Graceful degradation: without calibrated laser planes (or ``use_lasers=False``) the laser stage
is skipped; without an IMU calibration (or ``use_imu=False``) there is no rotation prior; a
frame that fails to track is not fused and adds no laser points (its result carries the last
good pose and ``track.ok = False``). Stage timings (ms) are in ``FrameResult.timings_ms``.

Defaults that depend on the resolution (used when the corresponding ``PipelineParams`` field is
``None``) are keyed on the *effective scale* ``s = processed width / 1280`` (the nominal camera),
which equals ``scale`` for the 1280x800 ELP module: depth ``DepthParams(block_size=7,
speckle_window_size=200)`` at s >= 0.75 (the vision module's full-resolution recommendation),
else ``DepthParams()`` (tuned for 640x400); lasers ``LaserParams().scaled(s)``; fusion
``FusionParams(voxel_mm=1.0, gyro_sigma_deg=0.02)`` at s >= 1 and ``1 / s`` mm voxels below
(2.0 mm at 0.5); tracking filter 6 px at s >= 0.75, else ``8 s`` px (4 px at 0.5); tracking
voxel ``max(2, fusion voxel)`` mm. So a 640x400 recording processed at ``scale=1`` gets the
half-resolution settings its pixels need.

``markerless`` world frame: the camera frame of the first successfully tracked frame. ``markers``
world frame: the frame that initialised the tag map. :meth:`FramePipeline.mesh` and the laser
cloud are in that world frame (mm).

A pipeline is meant for single-threaded use: it is not thread-safe (OpenCV matchers and the
Open3D voxel grids keep internal state). Use one instance per processing thread.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, Callable

import cv2
import numpy as np

from ohs.track.filter_gpu import TrackingFilterGpu
from ohs.track.fusion import LaserCloud
from ohs.track.imu_prior import GyroPredictor
from ohs.track.markerless import FusionParams, MarkerlessTracker
from ohs.track.markers import MarkerParams, MarkerTracker
from ohs.track.recon_tsdf import ConfidenceTsdf
from ohs.types import ImuSample, LaserPlane, ScannerCalibration, StereoFrame, TrackResult
from ohs.vision.depth import SIGMA_MODELS, DepthEstimator, DepthParams
from ohs.vision.laser import LINE_NAMES, LaserLines, LaserParams, extract_laser_lines, laser_points
from ohs.vision.rectify import Rectifier

__all__ = [
    "MODES",
    "PipelineParams",
    "FrameResult",
    "FramePipeline",
    "default_depth_params",
    "default_fusion_params",
    "default_laser_params",
    "default_track_filter_px",
    "effective_scale",
    "tracking_depth",
]

log = logging.getLogger(__name__)

MODES = ("markerless", "markers")
RECON_MODELS = ("auto", "confidence", "open3d")
NOMINAL_WIDTH = 1280  # px per camera of the ELP module; pixel-based defaults are tuned for it
GYRO_SIGMA_DEG = 0.02  # gyro rotation prior per frame used by the default fusion parameters
DETAIL_PROBE_FRAMES = 10  # frames at the start of a scan that also track on with_raw_depth ("Detailed parts")
# during them, a frame whose filtered depth holds its weakest translation less than this is a
# detailed part. On the simulator smooth parts hold >= 1.1e-3 there; detailed ones 1e-5 to 4e-5
# once the model has grown, but a young model flatters them (the carburetor's first frames: 3e-4,
# 2e-4, then 1e-4 and falling), so the threshold sits between
DETAIL_PROBE_STRENGTH = 5e-4


def effective_scale(image_width: int) -> float:
    """Processed image width relative to the nominal 1280 px (pixel density the defaults assume)."""
    return float(image_width) / NOMINAL_WIDTH


def default_depth_params(scale: float) -> DepthParams:
    """Depth settings for images at (effective) ``scale`` (the SGBM full-resolution recommendation at
    >= 0.75; the GPU matcher scales its own pixel quantities with the image width)."""
    if float(scale) >= 0.75:
        return DepthParams(block_size=7, speckle_window_size=200)
    return DepthParams()


def default_laser_params(scale: float) -> LaserParams:
    """Laser extraction settings for images at ``scale`` (pixel quantities scaled)."""
    return LaserParams() if float(scale) == 1.0 else LaserParams().scaled(float(scale))


def default_fusion_params(scale: float) -> FusionParams:
    """TSDF settings: 1 mm voxels at full resolution, ``1 / scale`` mm below (2 mm at 0.5), and a
    0.02 deg gyro prior (the module default of 0.1 deg lets camera-fixed depth errors win)."""
    s = float(scale)
    voxel = 1.0 if s >= 1.0 else round(1.0 / s, 1)
    return FusionParams(voxel_mm=voxel, gyro_sigma_deg=GYRO_SIGMA_DEG)


def default_track_filter_px(scale: float) -> float:
    """Gaussian sigma (px of the processed image) of :func:`tracking_depth`: 6 px at full resolution."""
    s = float(scale)
    return 6.0 if s >= 0.75 else max(1.0, 8.0 * s)


def tracking_depth(depth: np.ndarray, sigma_px: float, erode_mult: float = 2.0, max_change: float = 0.01,
                   edge_slope: float | None = None, work_size: tuple[int, int] | None = None) -> np.ndarray:
    """Depth map for ICP with the camera-fixed dot-scale error pattern suppressed (0 = invalid).

    1. Pixels on depth steps (``|grad z| > edge_slope * z`` per pixel; default 0.5 % at 1280 px
       width, scaled with the image width) are treated as invalid, so smoothing never mixes
       surfaces across occlusion edges. On full-resolution SGBM depth this also catches the small
       steps of the dot-scale error pattern (2-3 % of the pixels, in clusters), whose
       surroundings the erosion below then removes.
    2. Normalised Gaussian convolution with ``sigma_px`` over the valid pixels.
    3. The validity mask is eroded by ``erode_mult * sigma_px`` (the image border counts as
       invalid), and pixels the smoothing moved by more than ``max_change * z`` are dropped:
       near any boundary the normalised average is biased, and such biases are fixed to the
       camera too.

    ``work_size = (w, h)``, smaller than the input (the pipeline passes its tracking resolution,
    half the width at full quality), gives the same map at a fraction of the cost: step 1 still
    runs on the input pixels (a block average hides the steps of the error pattern, and keeping
    their surroundings measurably worsened tracking), then depth and validity are block-averaged
    to ``work_size`` (a pixel valid only where its whole block is; pixel-centre convention), steps
    2-3 run there with ``sigma_px`` and the erosion radius scaled by the same factor, and the
    smooth result is upsampled bilinearly back to the input resolution (valid where all four
    neighbours are). On the simulator this keeps the same pixels as the full-resolution filter
    to ~1 % of the image, with values within
    0.005 mm (std), and tracks as well (SGBM, Open3D model, the three validation scans: max
    errors 0.29 / 0.90 / 0.45 mm and 0.063 / 0.061 / 0.069 deg against 0.29 / 0.97 / 0.50 mm and
    0.077 / 0.063 / 0.085 deg) at 40 % of the cost (15 ms instead of 37 ms at 1280x800).

    ``sigma_px <= 0`` returns the input unchanged.
    """
    d = np.ascontiguousarray(depth, dtype=np.float32)
    if sigma_px <= 0:
        return d
    h0, w0 = d.shape
    slope = (0.005 * NOMINAL_WIDTH / w0) if edge_slope is None else float(edge_slope)
    gx = cv2.Sobel(d, cv2.CV_32F, 1, 0, ksize=3, scale=0.125)
    gy = cv2.Sobel(d, cv2.CV_32F, 0, 1, ksize=3, scale=0.125)
    s = float(sigma_px)
    if work_size is not None and (int(work_size[0]), int(work_size[1])) != (w0, h0):
        return _tracking_depth_small(d, gx, gy, slope, s, erode_mult, max_change, (int(work_size[0]), int(work_size[1])))
    valid = (d > 0) & (np.sqrt(gx * gx + gy * gy) <= slope * d)
    vf = valid.astype(np.float32)
    # np.where, not d * vf: a NaN pixel (no depth) would make the whole blur radius around it NaN
    num = cv2.GaussianBlur(np.where(valid, d, np.float32(0.0)), (0, 0), s, borderType=cv2.BORDER_CONSTANT)
    den = cv2.GaussianBlur(vf, (0, 0), s, borderType=cv2.BORDER_CONSTANT)
    out = num / np.maximum(den, 1e-6)
    # erosion by a disc of radius erode_mult * sigma, with the image border as invalid
    h, w = h0, w0
    pad = np.zeros((h + 2, w + 2), np.uint8)
    pad[1:-1, 1:-1] = valid
    dist = cv2.distanceTransform(pad, cv2.DIST_L2, 5)[1:-1, 1:-1]
    keep = (dist > erode_mult * s) & (np.abs(out - d) <= max_change * d)
    return np.where(keep, out, np.float32(0.0)).astype(np.float32)


def with_raw_depth(filtered: np.ndarray, raw: np.ndarray) -> np.ndarray:
    """``filtered`` (a :func:`tracking_depth` map) with the ``raw`` depth wherever the filter
    dropped a pixel that has depth: the tracker's input for detailed parts (module docstring)."""
    f = np.asarray(filtered, dtype=np.float32)
    r = np.asarray(raw, dtype=np.float32)
    m = (f <= 0) & np.isfinite(r) & (r > 0)
    if not m.any():
        return f
    out = f.copy()
    out[m] = r[m]
    return out


def _tracking_depth_small(d: np.ndarray, gx: np.ndarray, gy: np.ndarray, slope: float, sigma_px: float,
                          erode_mult: float, max_change: float, size: tuple[int, int]) -> np.ndarray:
    """:func:`tracking_depth` with ``work_size``: steps 2-3 at ``size``, the result back at the input size."""
    h0, w0 = d.shape
    w, h = size
    # step 1 on the input pixels (uint8 masks, 255 = valid)
    v8 = cv2.bitwise_and(cv2.compare(cv2.magnitude(gx, gy), d * np.float32(slope), cv2.CMP_LE),
                         cv2.compare(d, 0.0, cv2.CMP_GT))
    # block average where the whole block is valid (other blocks are dropped, so d needs no masking)
    valid = cv2.resize(v8, (w, h), interpolation=cv2.INTER_AREA) == 255
    dd = np.where(valid, cv2.resize(d, (w, h), interpolation=cv2.INTER_AREA), np.float32(0.0)).astype(np.float32)
    s = sigma_px * w / w0
    num = cv2.GaussianBlur(dd, (0, 0), s, borderType=cv2.BORDER_CONSTANT)
    den = cv2.GaussianBlur(valid.astype(np.float32), (0, 0), s, borderType=cv2.BORDER_CONSTANT)
    out = num / np.maximum(den, 1e-6)
    pad = np.zeros((h + 2, w + 2), np.uint8)
    pad[1:-1, 1:-1] = valid
    dist = cv2.distanceTransform(pad, cv2.DIST_L2, 5)[1:-1, 1:-1]
    keep = (dist > erode_mult * s) & (np.abs(out - dd) <= max_change * dd)
    res = np.where(keep, out, np.float32(0.0)).astype(np.float32)
    # back to the input size: bilinear, valid where all four neighbours are kept (their weights sum to 1)
    up = cv2.resize(res, (w0, h0), interpolation=cv2.INTER_LINEAR)
    ok = cv2.resize(keep.view(np.uint8) * np.uint8(255), (w0, h0), interpolation=cv2.INTER_LINEAR) == 255
    return cv2.bitwise_and(up, up, mask=ok.view(np.uint8))


@dataclass
class PipelineParams:
    """What :class:`FramePipeline` runs. ``None`` sub-parameters mean defaults for ``scale``."""

    mode: str = "markerless"  # "markerless" | "markers"
    scale: float = 1.0  # 1.0 offline, 0.5 live
    use_imu: bool = True
    use_lasers: bool = True
    integrate_depth: bool = True  # markers mode: fuse depth at the marker poses (markerless always fuses)
    depth: DepthParams | None = None  # None -> sensible defaults for this scale
    laser: LaserParams | None = None  # None -> LaserParams().scaled(scale)
    fusion: FusionParams | None = None  # None -> voxel 1.0 mm at scale >= 1, 2.0 mm at 0.5
    markers: MarkerParams | None = None
    # markerless tracking model (see the module docstring); None -> defaults for this scale
    track_filter_px: float | None = None  # 0 -> track on the raw depth in the reconstruction TSDF
    track_voxel_mm: float | None = None
    # reconstruction model (module docstring, "Reconstruction model"): "auto" = the confidence TSDF
    # on a GPU, else Open3D; "confidence" also without a GPU (numpy, slow); "open3d" = no confidence
    recon: str = "auto"
    # False: every frame comes with its pose (process(frame, pose)), so no tracking model is built
    tracking: bool = True

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if not (np.isfinite(self.scale) and 0.0 < float(self.scale) <= 1.0):
            raise ValueError(f"scale must be in (0, 1], got {self.scale!r}")
        if self.recon not in RECON_MODELS:
            raise ValueError(f"recon must be one of {RECON_MODELS}, got {self.recon!r}")


@dataclass
class FrameResult:
    """Everything one processed frame produced (images and depth are at the pipeline scale)."""

    index: int
    t: float
    left_r: np.ndarray
    right_r: np.ndarray
    depth: np.ndarray | None  # raw stereo depth (mm, 0 = invalid), the one that is fused
    lines: LaserLines | None  # left rectified image
    laser_points: dict[str, np.ndarray]  # frame C, mm, validated
    track: TrackResult
    timings_ms: dict[str, float]
    lines_right: LaserLines | None = None
    extra: dict = field(default_factory=dict)
    sigma: np.ndarray | None = None  # the depth's standard deviation (mm, 0 = no depth), confidence TSDF only


def _timed(fn: Callable[..., Any], *args: Any) -> tuple[Any, float]:
    """``(fn(*args), ms)`` (for work run in a thread)."""
    t0 = time.perf_counter()
    out = fn(*args)
    return out, _ms(t0)


def _ms(t0: float) -> float:
    return 1e3 * (time.perf_counter() - t0)


class FramePipeline:
    """Rectify -> depth -> lasers -> pose -> fusion for a stream of raw stereo frames.

    Single-threaded use; not thread-safe. See the module docstring for the stages and defaults.
    """

    def __init__(self, calib: ScannerCalibration, params: PipelineParams | None = None) -> None:
        self.calib = calib
        self.params = p = params if params is not None else PipelineParams()
        if p.mode not in MODES:  # PipelineParams validates too, but fields may have been reassigned
            raise ValueError(f"mode must be one of {MODES}, got {p.mode!r}")
        self.rectifier = Rectifier(calib.stereo, p.scale)
        self.K = self.rectifier.K
        self.baseline = self.rectifier.baseline
        self.image_size = self.rectifier.image_size
        s_eff = effective_scale(self.image_size[0])
        self.depth_params = p.depth if p.depth is not None else default_depth_params(s_eff)
        self.laser_params = p.laser if p.laser is not None else default_laser_params(s_eff)
        fusion = p.fusion if p.fusion is not None else default_fusion_params(s_eff)
        # the reconstruction model keeps the whole scan and makes the mesh: always Open3D
        self.fusion_params = replace(fusion, model_backend="open3d")
        self.marker_params = p.markers if p.markers is not None else MarkerParams()
        self.track_filter_px = float(p.track_filter_px if p.track_filter_px is not None else default_track_filter_px(s_eff))
        tv = p.track_voxel_mm if p.track_voxel_mm is not None else max(2.0, self.fusion_params.voxel_mm)
        # the tracking model keeps fusion.model_backend (default "auto": the GPU volume when there is a GPU)
        self.track_fusion_params = replace(fusion, voxel_mm=float(tv))
        # the tracking filter works at the model's ray-cast resolution (module docstring)
        w, h = self.image_size
        rs = float(fusion.raycast_scale)
        self.track_size = (max(1, int(round(w * rs))), max(1, int(round(h * rs))))

        self._need_depth = p.mode == "markerless" or bool(p.integrate_depth)
        if p.mode == "markerless" and not p.integrate_depth:
            log.info("integrate_depth=False has no effect in markerless mode (the fused model is the tracking reference)")
        self.depth_estimator = DepthEstimator(self.K, self.baseline, self.depth_params) if self._need_depth else None

        planes: dict[str, LaserPlane] = {}
        if p.use_lasers:
            planes = {n: pl for n, pl in (calib.lasers or {}).items() if n in LINE_NAMES}
            if not planes:
                log.info("no calibrated laser planes: laser triangulation is skipped")
        self.planes = planes

        # reconstruction TSDF (raw depth) and, in markerless mode, the tracking TSDF (filtered depth).
        # The confidence TSDF only reconstructs: with track_filter_px=0 the model also tracks, so Open3D.
        self.markerless: MarkerlessTracker | ConfidenceTsdf | None = None
        self.has_confidence = False
        if self._need_depth:
            single = p.mode == "markerless" and p.tracking and self.track_filter_px <= 0
            self.markerless = self._make_recon(p.recon, single_model=single)
            self.has_confidence = isinstance(self.markerless, ConfidenceTsdf)
        self.tracker: MarkerlessTracker | None = None
        if p.mode == "markerless" and p.tracking:
            if self.track_filter_px > 0:
                self.tracker = MarkerlessTracker(self.K, self.image_size, self.track_fusion_params)
            else:
                self.tracker = self.markerless
        self.markers: MarkerTracker | None = None
        if p.mode == "markers":
            self.markers = MarkerTracker(self.K, self.baseline, self.marker_params)

        self.gyro: GyroPredictor | None = None
        if p.mode == "markerless" and p.use_imu:
            if calib.imu is None:
                log.info("no IMU calibration: markerless tracking runs without a gyro prior")
            else:
                self.gyro = GyroPredictor(calib.imu)

        self.cloud = LaserCloud()
        self._laser_pool: ThreadPoolExecutor | None = None  # made at the first frame with lasers
        self._track_filter_gpu: TrackingFilterGpu | None | bool = None  # None: not tried yet; False: CPU
        self.gpu_fallbacks: dict[str, str] = {}  # GPU stages that failed during the run (now on the CPU): stage -> reason
        self._filter_pending: tuple | None = None  # (depth, the filter started for it), see _start_tracking_input
        # the start of a scan tracks a second time on with_raw_depth (module docstring, "Detailed parts")
        self._probe: MarkerlessTracker | None = None
        self._probe_left = DETAIL_PROBE_FRAMES
        #: laser points dropped because they disagree with the frame's dense depth, per line
        self.laser_rejected: dict[str, int] = {}
        self._trajectory: list[tuple[float, np.ndarray, bool]] = []
        self._t_prev: float | None = None
        self._n_frames = 0
        self._n_ok = 0
        self._closed = False
        log.info(
            "FramePipeline: %s, scale %.2f (%dx%d), depth %s, lasers %s, gyro prior %s, voxel %.1f mm, tracking %s",
            p.mode, p.scale, *self.image_size, "on" if self._need_depth else "off",
            "+".join(sorted(self.planes)) or "off", "on" if self.gyro is not None else "off",
            self.fusion_params.voxel_mm,
            (f"filtered depth (sigma {self.track_filter_px:.1f} px, filtered at {self.track_size[0]}x{self.track_size[1]}), "
             f"{self.track_fusion_params.voxel_mm:.1f} mm model, {self.model_status}"
             if self.dual_model else "raw depth" if p.mode == "markerless" else "markers"),
        )

    # ------------------------------------------------------------------ properties
    @property
    def mode(self) -> str:
        return self.params.mode

    @property
    def lasers_enabled(self) -> bool:
        return bool(self.planes)

    @property
    def gyro_enabled(self) -> bool:
        return self.gyro is not None

    @property
    def dual_model(self) -> bool:
        """True when markerless tracking uses its own (filtered-depth) TSDF."""
        return self.tracker is not None and self.tracker is not self.markerless

    @property
    def model_backend(self) -> str | None:
        """Backend of the markerless tracking model: ``"gpu"`` or ``"open3d"`` (``None`` in marker mode)."""
        return self.tracker.model_backend if self.tracker is not None else None

    @property
    def model_device(self) -> str | None:
        """Where the tracking model lives (GPU name or Open3D device); ``None`` in marker mode."""
        return self.tracker.model_device if self.tracker is not None else None

    @property
    def model_status(self) -> str:
        """Plain-language tracking-model choice, e.g. ``"GPU model on gfx1035 (384^3 voxels, 768 mm cube)"``."""
        if self.tracker is None:
            return ("no tracking model (frames come with poses)" if self.params.mode == "markerless"
                    else "no markerless tracking model (marker mode)")
        if not self.dual_model:
            return "Open3D model (single-model tracking makes the mesh)"
        return self.tracker.model_status

    def tracking_input(self, depth: np.ndarray) -> np.ndarray:
        """The tracking model's input: :func:`tracking_depth` of ``depth`` (sigma
        ``track_filter_px``), filtered at :attr:`track_size`, at the pipeline resolution. With a GPU
        (:func:`ohs.compute.get_device`) the filter runs there (:class:`ohs.track.filter_gpu.TrackingFilterGpu`,
        the same map to float rounding)."""
        pending, self._filter_pending = self._filter_pending, None
        f = self._track_filter_gpu
        if f is None:
            f = self._track_filter_gpu = self._make_track_filter_gpu(depth.shape)
        if f:
            try:
                if pending is not None and pending[0] is depth:
                    return f.finish(pending[1])
                del pending  # for another map: waited for and dropped
                return f(depth)
            except Exception as exc:  # noqa: BLE001 - a GPU failure must not stop the scan
                log.warning("tracking filter on the GPU failed (%s); filtering on the CPU", exc)
                self._track_filter_gpu = False
                self.gpu_fallbacks.setdefault("track_filter", str(exc))
        return tracking_depth(depth, self.track_filter_px, work_size=self.track_size)

    def _start_tracking_input(self, depth: np.ndarray) -> None:
        """Start the GPU tracking filter of ``depth``, the map the depth estimator just returned,
        without waiting for it: :meth:`tracking_input` of the same array then only collects it, and
        the device filters while the frame goes on on the host. Where the matcher left the map on
        the same device, the filter reads it there and nothing is uploaded. Does nothing without
        the GPU filter; a failure here is left to :meth:`tracking_input`, which filters again."""
        self._filter_pending = None
        f = self._track_filter_gpu
        if f is None:
            f = self._track_filter_gpu = self._make_track_filter_gpu(depth.shape)
        if not f:
            return
        try:
            on_dev = self.depth_estimator.device_depth() if self.depth_estimator is not None else None
            src = on_dev[1] if on_dev is not None and on_dev[0] is f.dev else depth
            self._filter_pending = (depth, f.start(src))
        except Exception as exc:  # noqa: BLE001
            log.debug("the tracking filter could not be started ahead (%s)", exc)

    def _make_track_filter_gpu(self, shape: tuple[int, ...]) -> TrackingFilterGpu | bool:
        """The GPU tracking filter for depth maps of ``shape``, or ``False`` (no GPU, or sizes it can't do)."""
        from ohs import compute  # noqa: PLC0415

        in_size = (int(shape[1]), int(shape[0]))
        if self.track_filter_px <= 0 or not TrackingFilterGpu.supports(in_size, self.track_size):
            return False
        dev = compute.get_device()
        if dev is None:
            return False
        try:
            return TrackingFilterGpu(dev, in_size, self.track_size, self.track_filter_px)
        except Exception as exc:  # noqa: BLE001
            log.warning("no tracking filter on the GPU (%s); filtering on the CPU", exc)
            return False

    @property
    def n_frames(self) -> int:
        """Frames processed since construction or the last :meth:`reset`."""
        return self._n_frames

    @property
    def n_tracked(self) -> int:
        """Frames whose pose was found (and which were fused)."""
        return self._n_ok

    # ------------------------------------------------------------------ input
    def add_imu(self, samples: list[ImuSample]) -> None:
        """Buffer IMU samples (``t_host`` in the IMU host clock, raw gyro in the IMU frame).

        Ignored when the gyro prior is off (no IMU calibration, ``use_imu=False`` or marker mode).
        Samples must arrive before the frames they cover (the buffer keeps the last 10 s).
        """
        if self.gyro is not None and samples:
            self.gyro.add(samples)

    def add_imu_arrays(self, t_host: np.ndarray, gyro: np.ndarray) -> None:
        """Like :meth:`add_imu` with arrays (``RecordingReader.imu_arrays()`` style)."""
        if self.gyro is not None and len(t_host):
            self.gyro.add_arrays(t_host, gyro)

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("FramePipeline is closed")

    # ------------------------------------------------------------------ processing
    def process(self, frame: StereoFrame, pose: np.ndarray | None = None) -> FrameResult:
        """Process one raw stereo frame (``t_host`` = camera timestamp).

        ``pose`` (optional, 4x4 ``T_wc``) skips tracking and fuses the frame at that pose, e.g. to
        reprocess with poses known from elsewhere. Raises ``ValueError`` if the raw image size
        does not match the calibration.
        """
        self._check_open()
        p = self.params
        t = float(frame.t_host)
        tim: dict[str, float] = {}
        extra: dict = {}
        n_fallbacks = len(self.gpu_fallbacks)
        t_start = time.perf_counter()

        t0 = time.perf_counter()
        left_r, right_r = self.rectifier.rectify(frame.left, frame.right)
        tim["rectify"] = _ms(t0)

        # the laser lines are extracted in two CPU threads while the depth runs (on the GPU, or in
        # SGBM's own threads): laser_extract is how long the frame then still waits for them,
        # laser_extract_cpu the extraction's own time
        futs = None
        if self.planes:
            if self._laser_pool is None:
                self._laser_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ohs-laser")
            futs = (self._laser_pool.submit(_timed, extract_laser_lines, left_r, self.laser_params),
                    self._laser_pool.submit(_timed, extract_laser_lines, right_r, self.laser_params)
                    if self.laser_params.stereo_check else None)

        depth = sigma = None
        if self.depth_estimator is not None:
            t0 = time.perf_counter()
            if self.has_confidence:
                depth, sigma = self.depth_estimator.compute_with_sigma(left_r, right_r)
            else:
                depth = self.depth_estimator.compute(left_r, right_r)
            tim["depth"] = _ms(t0)
            if self.depth_estimator.gpu_failure is not None:
                self.gpu_fallbacks.setdefault("depth", self.depth_estimator.gpu_failure)
            # the tracking filter starts now and is collected when the frame is tracked
            # (tracking_input): track_filter is how long the frame then still waits for it
            if self.dual_model and (pose is not None or p.mode == "markerless"):
                self._start_tracking_input(depth)

        lines = lines_r = None
        if futs is not None:
            t0 = time.perf_counter()
            lines, ms_l = futs[0].result()
            lines_r, ms_r = futs[1].result() if futs[1] is not None else (None, 0.0)
            tim["laser_extract"] = _ms(t0)
            tim["laser_extract_cpu"] = ms_l + ms_r

        # the laser points need the depth but not the pose: they are triangulated in a laser thread
        # while the frame is tracked and fused. laser_triangulate is how long the frame then still
        # waits for them (and adds them to the cloud), laser_triangulate_cpu their own time
        fut_pts = None
        rejected: dict[str, int] = {}
        if self.planes:
            fut_pts = self._laser_pool.submit(_timed, partial(
                laser_points, left_r, right_r, self.K, self.baseline, self.planes, self.laser_params,
                lines_left=lines, lines_right=lines_r, depth=depth, depth_sigma=sigma, rejected=rejected))

        t0 = time.perf_counter()
        if pose is not None:
            T = np.asarray(pose, dtype=np.float64).reshape(4, 4)
            track = TrackResult(T.copy(), True, {"method": "external"})
            if self.dual_model and depth is not None:
                self.tracker.integrate(self.tracking_input(depth), None, T)
            self._fuse(depth, T, t, tim, sigma)
        elif p.mode == "markerless":
            if self.tracker is None:
                raise ValueError("this pipeline does not track (PipelineParams.tracking=False): pass the frame's pose")
            track = self._track_markerless(depth, t, tim, extra, sigma)
        else:
            track = self.markers.track(left_r, right_r, t)
            if track.ok:
                self._fuse(depth, track.T_wc, t, tim, sigma)
        tim["track"] = _ms(t0)
        self._t_prev = t

        pts: dict[str, np.ndarray] = {}
        if fut_pts is not None:
            t0 = time.perf_counter()
            pts, tim["laser_triangulate_cpu"] = fut_pts.result()
            for name, n in rejected.items():
                self.laser_rejected[name] = self.laser_rejected.get(name, 0) + n
            if track.ok:
                for name, P in pts.items():
                    self.cloud.add(P, track.T_wc, name)
            tim["laser_triangulate"] = _ms(t0)
            tim["laser"] = tim["laser_extract"] + tim["laser_triangulate"]
        tim["total"] = _ms(t_start)
        if len(self.gpu_fallbacks) > n_fallbacks:  # a GPU stage failed on this frame: the rest runs on the CPU
            extra["gpu_fallback"] = dict(list(self.gpu_fallbacks.items())[n_fallbacks:])

        self._n_frames += 1
        self._n_ok += int(track.ok)
        self._trajectory.append((t, np.asarray(track.T_wc, dtype=np.float64).copy(), bool(track.ok)))
        return FrameResult(int(frame.index), t, left_r, right_r, depth, lines, pts, track, tim, lines_r, extra, sigma)

    def _make_recon(self, recon: str, single_model: bool) -> MarkerlessTracker | ConfidenceTsdf:
        """The reconstruction model for ``PipelineParams.recon`` (module docstring, "Reconstruction model")."""
        fp = self.fusion_params
        want = recon
        if single_model and recon != "open3d":
            if recon == "confidence":
                log.info("recon \"confidence\" needs the separate tracking model (track_filter_px > 0): using Open3D")
            want = "open3d"
        if want in ("auto", "confidence"):
            try:
                # the matcher's sigma factor next to missing depth, which the model takes out again
                de = self.depth_estimator
                sm = SIGMA_MODELS["gpu" if de is not None and de.backend == "gpu" else "cpu"]
                return ConfidenceTsdf(self.K, self.image_size, voxel_mm=fp.voxel_mm, trunc_mm=fp.trunc_mm,
                                    depth_min_mm=fp.depth_min_mm, depth_max_mm=fp.depth_max_mm,
                                    backend="gpu" if want == "auto" else "auto",
                                    hole_factor=(sm.hole_amp, sm.hole_r_px))
            except RuntimeError as e:  # "auto" without a GPU
                log.info("reconstruction with Open3D on the CPU: %s", e)
        return MarkerlessTracker(self.K, self.image_size, self.fusion_params)

    @property
    def recon_status(self) -> str:
        """What makes the mesh, in plain words."""
        m = self.markerless
        if m is None:
            return "none"
        if isinstance(m, ConfidenceTsdf):
            where = f"on the GPU ({m.device})" if m.device else "on the CPU"
            return f"TSDF with per-vertex confidence, {where}"
        return "Open3D TSDF (every frame weighs the same)"

    def _fuse(self, depth: np.ndarray | None, T_wc: np.ndarray, t: float, tim: dict[str, float],
              sigma: np.ndarray | None = None) -> None:
        """Integrate the raw depth (and its sigma, for the confidence TSDF) into the reconstruction TSDF at ``T_wc``."""
        if self.markerless is None or depth is None:
            return
        ti = time.perf_counter()
        self.markerless.integrate(depth, sigma if self.has_confidence else None, T_wc, t=t)
        tim["integrate"] = _ms(ti)

    def _track_markerless(self, depth: np.ndarray, t: float, tim: dict[str, float], extra: dict,
                          sigma: np.ndarray | None = None) -> TrackResult:
        R_prior = None
        if self.gyro is not None and self._t_prev is not None and self.gyro.covers(self._t_prev, t):
            R_prior = self.gyro.relative_rotation(self._t_prev, t)
        if not self.dual_model:
            res = self.tracker.track(depth, None, t, R_prior)
        else:
            tf = time.perf_counter()
            d_track = self.tracking_input(depth)
            tim["track_filter"] = _ms(tf)
            # counted on the comparison: numpy counts a bool array several times faster than floats
            extra["track_valid_fraction"] = float(np.count_nonzero(d_track != 0)) / d_track.size
            # where the filtered depth can't hold the pose (a detailed part: the filter erodes it
            # away), the tracker uses it with the raw depth wherever the filter dropped a pixel
            if self._probe_left > 0:
                res = self._probe_track(d_track, depth, t, R_prior)
            else:
                res = self.tracker.track(d_track, None, t, R_prior, alt_depth=lambda: with_raw_depth(d_track, depth))
            if res.ok:
                self._fuse(depth, res.T_wc, t, tim, sigma)
        for k, v in res.info.get("timings_ms", {}).items():
            if k == "total":
                continue
            tim["track_integrate" if (k == "integrate" and self.dual_model) else k] = float(v)
        return res

    def _probe_track(self, d_track: np.ndarray, depth: np.ndarray, t: float, R_prior: np.ndarray | None) -> TrackResult:
        """A frame at the start of a scan (module docstring, "Detailed parts"): tracked on the
        filtered depth alone, and by a second tracker on :func:`with_raw_depth`. If the filtered
        depth can't hold the pose (a detailed part), the second tracker, consistent from the first
        frame on, takes over for the whole scan; after ``DETAIL_PROBE_FRAMES`` it is dropped."""
        if self._probe is None:
            self._probe = MarkerlessTracker(self.K, self.image_size, self.track_fusion_params)
        res = self.tracker.track(d_track, None, t, R_prior)
        res_p = self._probe.track(with_raw_depth(d_track, depth), None, t, R_prior)
        self._probe_left -= 1
        weak = not res.info.get("first_frame") and (
            (not res.ok and res_p.ok)
            or (res.ok and res.info.get("trans_strength", 1.0) < DETAIL_PROBE_STRENGTH))
        if weak:
            log.info("the filtered depth can't hold the pose at frame %d (a detailed part): this scan tracks "
                     "with the raw depth where the filter drops it", self._n_frames)
            self.tracker.close()
            self.tracker, self._probe, self._probe_left = self._probe, None, 0
            self.tracker.detail_mode, self.tracker.detail_frame = True, self._n_frames
            res_p.info["input"] = "alt"
            return res_p
        if self._probe_left <= 0:
            self._end_probe()
        return res

    def _end_probe(self) -> None:
        if self._probe is not None:
            self._probe.close()
            self._probe = None
        self._probe_left = 0

    # ------------------------------------------------------------------ results
    def mesh(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Marching-cubes mesh of the fused TSDF: ``(V (N,3), F (M,3), N (N,3))``, world frame, mm."""
        self._check_open()
        empty = (np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros((0, 3)))
        if self.markerless is None or self.markerless.n_integrated == 0:
            return empty
        try:
            return self.markerless.extract_mesh()
        except RuntimeError as exc:  # Open3D raises instead of returning an empty mesh
            log.warning("mesh extraction failed (%s); returning an empty mesh", exc)
            return empty

    def mesh_with_sigma(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray | None]:
        """:meth:`mesh` plus each vertex's standard deviation (mm, float32), or ``None`` when the
        reconstruction is Open3D's (no confidence)."""
        if not self.has_confidence:
            V, F, N = self.mesh()
            return V, F, N, None
        self._check_open()
        empty = (np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros((0, 3)), np.zeros(0, np.float32))
        if self.markerless.n_integrated == 0:
            return empty
        try:
            return self.markerless.extract_mesh(with_sigma=True)
        except RuntimeError as exc:
            log.warning("mesh extraction failed (%s); returning an empty mesh", exc)
            return empty

    def points(self) -> tuple[np.ndarray, np.ndarray]:
        """TSDF zero-crossing points and normals ``(P, N)`` (world, mm), e.g. for Poisson meshing."""
        self._check_open()
        empty = (np.zeros((0, 3)), np.zeros((0, 3)))
        if self.markerless is None or self.markerless.n_integrated == 0:
            return empty
        try:
            return self.markerless.extract_points()
        except RuntimeError as exc:
            log.warning("point extraction failed (%s); returning no points", exc)
            return empty

    def laser_cloud(self) -> LaserCloud:
        """The accumulated laser points (world frame, mm)."""
        return self.cloud

    def trajectory(self) -> list[tuple[float, np.ndarray, bool]]:
        """``[(t, T_wc, ok), ...]`` for every processed frame (failed frames carry the last good pose)."""
        return [(t, T.copy(), ok) for t, T, ok in self._trajectory]

    def reset(self) -> None:
        """Start a new scan: forget the models, the marker map, the laser cloud and the trajectory.

        Calibration, parameters and buffered IMU samples are kept.
        """
        self._check_open()
        if self.markerless is not None:
            self.markerless.reset()
        if self.dual_model:
            self.tracker.reset()
        self._end_probe()
        self._probe_left = DETAIL_PROBE_FRAMES
        if self.markers is not None:
            self.markers.reset()
        self.cloud.clear()
        self.laser_rejected = {}
        self._trajectory = []
        self._t_prev = None
        self._n_frames = 0
        self._n_ok = 0

    def close(self) -> None:
        """Release the voxel grids and trackers. The pipeline cannot be used afterwards."""
        self._closed = True
        if self._laser_pool is not None:
            self._laser_pool.shutdown(wait=True)
            self._laser_pool = None
        self._filter_pending = None  # a filter that was started and not collected is waited for here
        if isinstance(self._track_filter_gpu, TrackingFilterGpu):
            self._track_filter_gpu.close()
        self._track_filter_gpu = False
        self._end_probe()
        for tr in (self.tracker, self.markerless):
            if tr is not None:
                tr.close()
        self.markerless = None
        self.tracker = None
        self.markers = None
        self.depth_estimator = None
        self.gyro = None

    def __enter__(self) -> "FramePipeline":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
