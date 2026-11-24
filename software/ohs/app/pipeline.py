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
   points it drops, per line). Every calibrated plane whose name is a laser line is used; with
   two planes of a family (four lasers) the extraction looks for two lines of it
   (``LaserParams.lines_per_family``, set from the planes) and ``laser_points`` decides which
   plane each point is on.
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

**The scan's start** (``PipelineParams.start_frames``, ``start_sweeps``, ``start_predict``, ``start_first``,
``start_together``, ``start_detail``; ENGINE_PLAN X4b; off unless set). The first frame of a scan is registered
against a model made of one frame from a prediction of zero motion, and on the simulator it landed 1 to 8 mm off
on every scene with a table; the next frames are registered against a model that holds that error, and keep it (the
dark bracket's whole 3.9 mm). With ``start_frames`` = k > 1 the first k frames are tracked as before (their poses are what a live
view shows meanwhile) but held back: nothing of them is fused into the mesh's model and their laser points wait.
After the k-th (or a frame that fails, or when the scan is read: :meth:`FramePipeline.finish_start`) each frame is
registered again (:meth:`~ohs.track.markerless.MarkerlessTracker.register`) against a tracking model of all the
others at their poses, in ``start_sweeps`` rounds, from its own pose or (``start_predict`` "fit") from a fit in
time of the others' positions (a quadratic), its rotation held as the gyro's; with ``start_first`` the first frame
too, the world then put back at its camera. In a round the frames are registered one after the other, each against
the others' newest poses; with ``start_together`` all against the others' poses from before the round, and then
moved together. Every registration on the simulator's brackets lands about 0.2 mm off the others in the same
direction (along the table, where the translation is weakly held); one after the other, the frames registered later
take that bias on top of the earlier ones' and frames 1 to k-1 end up off frame 0 (the speckled bracket 0.45 mm,
then held by the rest of the scan); together, a bias all of them share moves them alike and is gone when the world
is put back at frame 0's camera. The tracking model is then made again from the k frames at those poses
(:meth:`~ohs.track.markerless.MarkerlessTracker.restart`, which also carries the last step into the next
prediction), the frames are fused into the mesh's model and their laser points added, and
:meth:`FramePipeline.trajectory` gives their new poses. With ``start_detail`` "window" the detail probe (above)
runs over the whole start and decides once at its end, on the median ``trans_strength`` of the filtered depth's
frames (or a frame only the raw depth held), instead of at the first weak frame, which is usually the first one,
judged on a model of one frame. :attr:`FramePipeline.start_report` says what the start did. The GPU tracking
model only: on the Open3D model (no GPU) the same start ("fit", ``start_first``) took an 8-frame test recording
108 mm off, so there the settings are not used and the results are as before.

From the CPU matcher (SGBM) the raw depth added this way leaves out every pixel within
``DETAIL_HOLE_PX_1280`` = 6 px (at 1280 px width, scaled with the width) of a pixel without depth
(:func:`without_hole_borders`). SGBM's depth next to its holes is biased (on the simulated bracket
+0.3 to +0.5 mm within 6 px, +0.19 mm farther in), and its holes lie where the projected dots leave
gaps, which move with the scanner: a camera-fixed error that pulled each frame towards the previous
pose. The tracker turned 6.5 % less than the scanner every frame and the 90-frame bracket ended
3.6 deg and 9.4 mm off; with the rule 0.58 deg and 0.95 mm (``MAINTENANCE_PLAN.md`` F13). When the CPU
matcher averages the pair with the pair upside down (``DepthParams.flip_average``, on by default on the CPU
path at full resolution since 2026-11-20, "The CPU path's defaults" below; most of
that bias was SGBM's paths coming only from above, :mod:`ohs.vision.depth`) the bias is confined to 2 px of
a hole, and ``DETAIL_HOLE_PX_FLIP_1280`` = 2 px are left out instead (``MAINTENANCE_PLAN.md`` F14). The GPU
matcher's depth has no such bias (its depth is used as it is). The mesh model fuses the whole depth.

**The CPU path's defaults** (since 2026-11-20; ``MAINTENANCE_PLAN.md`` F14, the owner's decision D7).
Without a GPU, a scan of a dark or plain part slid tens to hundreds of mm, and the matcher read 0.08 px too
far. When the depth comes from the CPU matcher and the tracking model is Open3D's (a separate tracking
model, markerless mode), three settings left ``None`` are filled in here:

* ``DepthParams.flip_average`` on (:mod:`ohs.vision.depth`, "Paths from above only"), with
  ``DETAIL_HOLE_PX_FLIP_1280`` = 2 px for detail mode;
* the velocity guard, ``FusionParams.velocity_max_accel`` = ``VELOCITY_MAX_ACCEL`` (2 m/s^2;
  :mod:`ohs.track.markerless`, "The velocity guard");
* ``FusionParams.raycast_weight_threshold`` = 8 (``CPU_RAYCAST_WEIGHT_THRESHOLD``; 3 before).

The flip average and the weight threshold apply from an effective scale of ``CPU_DEFAULTS_MIN_SCALE`` = 0.75
on (960 px wide and more, the boundary of the full-resolution SGBM settings): at 640 x 400 the matcher's
bias is 0.01 px, so the flip would double its time for nothing, and the weight threshold 8 made the 8-frame
half-resolution recording a little worse. The guard applies at every size (on that recording it never
acts). Each stays an option: ``flip_average=False``, ``velocity_max_accel=0`` and an explicit
``raycast_weight_threshold`` keep what is set. Elsewhere (the GPU path, a GPU matcher with the Open3D
model, the single-model tracking of ``track_filter_px=0``, marker mode, fusing at given poses with
``tracking=False``) the ``None`` settings become what
they were before (no flip, no guard, 3), so those results are as before. A report shows what was on in
``params.depth.flip_average`` and ``params.fusion`` (``velocity_max_accel``, ``raycast_weight_threshold``).
On the simulated scans (true calibration, last frame) the bracket went from 0.95 to 0.20 mm, the dark
bracket from 167 to 42 mm and from 249 to 0.9 mm (two seeds), the speckled one from 23 to 5.4 mm, the
0.5 mm plates from 36 and 50 to 5.7 mm, and the laser lines from 0.06-0.14 to under 0.02 mm off the mesh.

**Model backends.** The tracking model may live on the GPU
(:class:`~ohs.track.volume_gpu.GpuTsdfVolume`, a rolling cube): it takes
``fusion.model_backend`` (default ``"auto"``: the GPU when :func:`ohs.compute.get_device` finds
one); the single model of ``track_filter_px=0`` uses Open3D. :attr:`FramePipeline.model_backend`
says which backend tracks (``"gpu"`` / ``"open3d"``), :attr:`FramePipeline.model_device` where.
On the GPU the tracking model truncates at ``TRACK_GPU_TRUNC_MULT`` = 2 voxels (4 mm), on Open3D
at ``fusion.trunc_mult`` = 4 voxels (8 mm). A TSDF writes "inside" as far as its truncation behind
every surface it sees, so with 8 mm a wall thinner than that swells once its far side is seen (a
4 mm wall by 0.8 mm a side); frames registered to the swollen model put the cameras 0.3-0.5 % too
far out, and the mesh came out about 0.3 mm too fat. The Open3D model keeps 8 mm because it
tracks worse on stereo depth with less (``VALIDATION.md``, "Where a tracked scan's shape errors
come from"). The GPU model also stops writing through thin walls and past edges once it knows a
place: a voxel that was integrated 4 times takes nothing more from views that see it more than
``TRACK_GPU_BACK_VOXELS`` = 1 voxel (2 mm) behind another surface, at right angles to it
(:mod:`ohs.track.volume_gpu`, "The band behind the surface"). On the simulated bracket that took a
tracked scan from 16 % to 13 % more than 0.3 mm off and its mean offset from +0.12 to +0.01 mm
(``VALIDATION.md``, "Thin walls with tracking"). ``fusion.gpu_back_voxels`` replaces the 1; a value
of ``gpu_trunc_mult`` or more turns the rule off.

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

**Thin walls** (``PipelineParams.thin_walls``). A TSDF marks the space behind every surface it sees
as inside, 4 voxels deep in the mesh model, so the two sides of a wall thinner than that write
through it and the wall swells, and so do faces next to a convex edge. With the short band behind
the surface (:mod:`ohs.track.recon_tsdf`, "The band behind the surface") the mesh model takes an
observation in full only 2 voxels deep, and 1 voxel deep where it is seen from opposite sides
("Seen from opposite sides" there; :attr:`FramePipeline.opposite_mm`). ``"auto"`` (the default) turns it on in a markerless scan
and leaves marker mode as it was: there the frames disagree by about 0.9 mm, and the whole band is
what averages them. ``"on"`` and ``"off"`` force it. Only the confidence TSDF has the rule, so a
mesh made by Open3D's model (no GPU, or ``recon="open3d"``) is as before whatever is set;
:attr:`FramePipeline.thin_walls_mm` says what the mesh model uses. On the simulated bracket the
share of a tracked scan more than 0.3 mm off fell from 20 % to 16.5 % and a 2 mm flange read 3.0
instead of 3.4 mm, with 11-29 % more pinholes in the raw mesh (``VALIDATION.md``, "A band that is
short only behind the surface"); seen from opposite sides, a tracked 2 mm flange then read
2.26-2.61 instead of 2.72-2.98 mm, with the same pinholes (``VALIDATION.md``, "Thin walls seen
from opposite sides").

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
from ohs.track.markerless import RAYCAST_WEIGHT_THRESHOLD, VELOCITY_MAX_ACCEL, FusionParams, MarkerlessTracker
from ohs.track.markers import MarkerParams, MarkerTracker
from ohs.track.recon_tsdf import (BACK_VOXELS, GUARD_PX_1280, OPPOSITE_VOXELS, SIGMA_GUARD_PX_1280, ConfidenceTsdf,
                                  gap_guard_mask)
from ohs.types import ImuSample, LaserPlane, ScannerCalibration, StereoFrame, TrackResult
from ohs.vision.depth import SIGMA_MODELS, DepthEstimator, DepthParams
from ohs.vision.laser import (LaserLines, LaserParams, compiled_link_rows, extract_laser_lines, is_line_name,
                              laser_points, lines_per_family)
from ohs.vision.rectify import Rectifier

__all__ = [
    "MODES",
    "THIN_WALLS",
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
THIN_WALLS = ("auto", "on", "off")  # PipelineParams.thin_walls
START_PREDICT = ("pose", "fit")  # PipelineParams.start_predict (module docstring, "The scan's start")
START_DETAIL = ("frame", "window")  # PipelineParams.start_detail
NOMINAL_WIDTH = 1280  # px per camera of the ELP module; pixel-based defaults are tuned for it
GYRO_SIGMA_DEG = 0.02  # gyro rotation prior per frame used by the default fusion parameters
# truncation of the tracking model on the GPU, in voxels (4 mm at its 2 mm voxels; module docstring, "Model backends")
TRACK_GPU_TRUNC_MULT = 2.0
TRACK_GPU_BACK_VOXELS = 1.0  # the GPU tracking model's band behind the surface for established voxels (module docstring)
# with thin walls on, the mesh model's second mean for voxels seen from opposite sides: its cut in voxels
# (ohs.track.recon_tsdf, "Seen from opposite sides"); None turns it off
THIN_WALLS_OPPOSITE_VOXELS: float | None = OPPOSITE_VOXELS
DETAIL_PROBE_FRAMES = 10  # frames at the start of a scan that also track on with_raw_depth ("Detailed parts")
# during them, a frame whose filtered depth holds its weakest translation less than this is a
# detailed part. On the simulator smooth parts hold >= 1.1e-3 there; detailed ones 1e-5 to 4e-5
# once the model has grown, but a young model flatters them (the carburetor's first frames: 3e-4,
# 2e-4, then 1e-4 and falling), so the threshold sits between
DETAIL_PROBE_STRENGTH = 5e-4
# detail mode on the CPU matcher's depth: its raw depth within this many px (at 1280 px width, scaled with the
# width) of a pixel without depth is left out (module docstring, "Detailed parts"; MAINTENANCE_PLAN.md F13)
DETAIL_HOLE_PX_1280 = 6.0
# ... when the CPU matcher averages the pair with the pair upside down (DepthParams.flip_average): the bias
# next to a hole is then confined to 2 px (ohs.vision.depth, "Paths from above only"; MAINTENANCE_PLAN.md F14)
DETAIL_HOLE_PX_FLIP_1280 = 2.0
# the CPU path's defaults (module docstring, "The CPU path's defaults"): the flip average and the weight
# threshold 8 from this effective scale on (960 px wide and more: the full-resolution SGBM settings' boundary)
CPU_DEFAULTS_MIN_SCALE = 0.75
CPU_RAYCAST_WEIGHT_THRESHOLD = 8.0


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


def without_hole_borders(depth: np.ndarray, radius_px: int) -> np.ndarray:
    """``depth`` without its pixels within ``radius_px`` (in x and in y: a square) of a pixel without depth
    (0 or not finite); the image border is no hole. The CPU matcher's raw depth for detail mode (module
    docstring, "Detailed parts"). ``radius_px <= 0`` returns the input itself."""
    d = np.asarray(depth, dtype=np.float32)
    r = int(radius_px)
    if r <= 0:
        return d
    valid = (np.isfinite(d) & (d > 0)).astype(np.uint8)
    keep = cv2.erode(valid, np.ones((2 * r + 1, 2 * r + 1), np.uint8))  # outside the image counts as depth
    return np.where(keep > 0, d, np.float32(0.0)).astype(np.float32)


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
    # the Open3D tracking model's truncation (voxels; ENGINE_PLAN X0a, the pull along a table); None: fusion.trunc_mult,
    # as the mesh's model. The GPU tracking model has its own (FusionParams.gpu_trunc_mult) and ignores it.
    track_trunc_mult: float | None = None
    # detail mode on the CPU matcher's depth (module docstring, "Detailed parts"): the raw depth it adds leaves out
    # this many px (at 1280 px wide) along the image's border, where that matcher reads 0.13 mm off (ENGINE_PLAN
    # X0a); None or 0: off. The GPU matcher's raw depth is used as it is.
    detail_border_px: float | None = None
    # reconstruction model (module docstring, "Reconstruction model"): "auto" = the confidence TSDF
    # on a GPU, else Open3D; "confidence" also without a GPU (numpy, slow); "open3d" = no confidence
    recon: str = "auto"
    # the gap guard of the reconstruction (ohs.track.recon_tsdf, "The gap guard"): depth within this
    # many px of a patch without depth is not fused, which keeps holes open (VALIDATION.md, "The gap
    # guard in the engine"); None -> 8 px at 1280 px width (the default); 0 -> off
    gap_guard_px: float | None = None
    # the guard by the matcher's own doubt (ohs.track.recon_tsdf, "The guard by the matcher's own doubt";
    # ENGINE_PLAN X1): depth within gap_guard_sigma_px of a patch without depth whose matcher sigma is above
    # gap_guard_sigma_mm is not fused either. None or 0: off. gap_guard_sigma_px None -> 16 px at 1280 px
    # width; gap_guard_sigma_angle False: the matcher's factor for a steep view taken out of the sigma first.
    # Only the confidence TSDF has the sigma: the Open3D reconstruction (the no-GPU path, or recon "open3d")
    # ignores these three, and its results do not change with them.
    gap_guard_sigma_mm: float | None = None
    gap_guard_sigma_px: float | None = None
    gap_guard_sigma_angle: bool = True
    # the mesh model's short band behind the surface (module docstring, "Thin walls"): "auto" = on
    # in a markerless scan and off in marker mode; "on" and "off" force it
    thin_walls: str = "auto"
    # False: every frame comes with its pose (process(frame, pose)), so no tracking model is built
    tracking: bool = True
    # The scan's start (module docstring, "The scan's start"; ENGINE_PLAN X4b (a), (b)). start_frames > 1: the first
    # that many frames are tracked as before but held back (not fused into the mesh's model, their laser points kept
    # back), registered again against a model of the others in start_sweeps rounds (each from its pose, or with
    # start_predict "fit" from a fit in time of the others' positions), the tracking model is made again from them,
    # and only then are they fused. 0 or 1: off (every frame fused as it is tracked, as before).
    start_frames: int = 0
    start_sweeps: int = 2
    start_predict: str = "pose"
    # ... the first frame registered too in those rounds (against the others), the world then put back at its camera
    start_first: bool = False
    # ... in a round every frame registered against the others' poses from before the round, then all moved together
    # (False: one after the other, each against the others' newest poses, as before)
    start_together: bool = False
    # ... only in a scan that tracks in detail mode (the filtered depth does not hold its translation); a smooth
    # part's start is then fused as it was tracked
    start_detail_only: bool = False
    # when the tracker's detail mode is decided: "frame" (as before: at the first weak frame of the first
    # DETAIL_PROBE_FRAMES) or "window" (once, at the end of the start_frames, on their median trans_strength)
    start_detail: str = "frame"

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if not (np.isfinite(self.scale) and 0.0 < float(self.scale) <= 1.0):
            raise ValueError(f"scale must be in (0, 1], got {self.scale!r}")
        if self.recon not in RECON_MODELS:
            raise ValueError(f"recon must be one of {RECON_MODELS}, got {self.recon!r}")
        if self.gap_guard_px is not None and not float(self.gap_guard_px) >= 0:
            raise ValueError(f"gap_guard_px must be >= 0, got {self.gap_guard_px!r}")
        if self.gap_guard_sigma_mm is not None and not float(self.gap_guard_sigma_mm) >= 0:
            raise ValueError(f"gap_guard_sigma_mm must be >= 0 (0 or None: off), got {self.gap_guard_sigma_mm!r}")
        if self.gap_guard_sigma_px is not None and not float(self.gap_guard_sigma_px) >= 0:
            raise ValueError(f"gap_guard_sigma_px must be >= 0, got {self.gap_guard_sigma_px!r}")
        if self.thin_walls not in THIN_WALLS:
            raise ValueError(f"thin_walls must be one of {THIN_WALLS}, got {self.thin_walls!r}")
        if self.track_trunc_mult is not None and not float(self.track_trunc_mult) >= 1.0:
            raise ValueError(f"track_trunc_mult must be at least 1 (voxel), got {self.track_trunc_mult!r}")
        if self.detail_border_px is not None and not float(self.detail_border_px) >= 0:
            raise ValueError(f"detail_border_px must be >= 0, got {self.detail_border_px!r}")
        if int(self.start_frames) < 0 or int(self.start_sweeps) < 0:
            raise ValueError(f"start_frames and start_sweeps must be >= 0, got {self.start_frames!r}, {self.start_sweeps!r}")
        if self.start_predict not in START_PREDICT:
            raise ValueError(f"start_predict must be one of {START_PREDICT}, got {self.start_predict!r}")
        if self.start_detail not in START_DETAIL:
            raise ValueError(f"start_detail must be one of {START_DETAIL}, got {self.start_detail!r}")


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


@dataclass
class _Held:
    """A frame of the scan's start, held back until the start is over (module docstring, "The scan's start")."""

    pos: int  # its place in the pipeline's trajectory
    t: float
    d_track: np.ndarray  # the tracking model's input (filtered depth)
    alt: np.ndarray  # ... with the raw depth where the filter dropped it (detail mode's input)
    depth: np.ndarray  # what the mesh's model fuses
    sigma: np.ndarray | None
    gyro: bool  # it came with a gyro rotation
    res: list = field(default_factory=list)  # [(tracker, its TrackResult)] for every tracker that tracked it
    pts: dict[str, np.ndarray] = field(default_factory=dict)  # its laser points (camera frame)

    def of(self, tracker: Any) -> TrackResult | None:
        """The result of ``tracker`` for this frame (None: it did not track it)."""
        return next((r for tr, r in self.res if tr is tracker), None)


def _fit_start(P: list[np.ndarray], times: list[float], j: int) -> np.ndarray:
    """``P[j]``'s rotation at the position a fit in time of the other poses' positions gives for ``times[j]``: a
    quadratic with three or more others, else a line (``PipelineParams.start_predict`` "fit")."""
    others = [i for i in range(len(P)) if i != j]
    t = np.array([times[i] - times[j] for i in others], dtype=np.float64)
    C = np.array([P[i][:3, 3] for i in others], dtype=np.float64)
    deg = 2 if len(others) >= 3 else 1
    A = np.vander(t, deg + 1, increasing=True)
    coef = np.linalg.lstsq(A, C, rcond=None)[0]
    T = np.asarray(P[j], dtype=np.float64).copy()
    T[:3, 3] = coef[0]
    return T


def _timed(fn: Callable[..., Any], *args: Any) -> tuple[Any, float]:
    """``(fn(*args), ms)`` (for work run in a thread)."""
    t0 = time.perf_counter()
    out = fn(*args)
    return out, _ms(t0)


def _lines_then_points(futs: tuple, points: Callable[..., dict]) -> tuple:
    """In a laser thread: collect the two extractions ``futs`` (each ``(lines, ms)``; the right one
    may be None), then ``points(lines_left=..., lines_right=...)``. Returns ``(lines, lines_right,
    points, ms of both extractions, ms of the points, when the lines were there)``."""
    lines, ms_l = futs[0].result()
    lines_r, ms_r = futs[1].result() if futs[1] is not None else (None, 0.0)
    t_lines = time.perf_counter()
    pts = points(lines_left=lines, lines_right=lines_r)
    return lines, lines_r, pts, ms_l + ms_r, _ms(t_lines), t_lines


def _ms(t0: float) -> float:
    return 1e3 * (time.perf_counter() - t0)


class FramePipeline:
    """Rectify -> depth -> lasers -> pose -> fusion for a stream of raw stereo frames.

    Single-threaded use; not thread-safe. See the module docstring for the stages and defaults.
    """

    def __init__(self, calib: ScannerCalibration, params: PipelineParams | None = None,
                 model: MarkerlessTracker | ConfidenceTsdf | None = None) -> None:
        """``model``: a reconstruction model to fuse into instead of a new one, taken from another
        pipeline with :meth:`release_model` and switched to this pipeline's camera; the frames are
        then fused at poses given (``params.tracking`` must be off). A merge of scans of different
        calibrations passes one model on from scan to scan this way. The model should have been
        made with the same fusion settings (``params.fusion``)."""
        self.calib = calib
        self.params = p = params if params is not None else PipelineParams()
        if p.mode not in MODES:  # PipelineParams validates too, but fields may have been reassigned
            raise ValueError(f"mode must be one of {MODES}, got {p.mode!r}")
        if model is not None and p.tracking:
            raise ValueError("a model passed on from another pipeline only fuses: set PipelineParams.tracking=False")
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
        # and, there, the short truncation (an explicit fusion.gpu_trunc_mult wins)
        self.track_fusion_params = replace(
            fusion, voxel_mm=float(tv),
            gpu_trunc_mult=TRACK_GPU_TRUNC_MULT if fusion.gpu_trunc_mult is None else fusion.gpu_trunc_mult,
            gpu_back_voxels=TRACK_GPU_BACK_VOXELS if fusion.gpu_back_voxels is None else fusion.gpu_back_voxels)
        # the tracking filter works at the model's ray-cast resolution (module docstring)
        w, h = self.image_size
        rs = float(fusion.raycast_scale)
        self.track_size = (max(1, int(round(w * rs))), max(1, int(round(h * rs))))

        self._need_depth = p.mode == "markerless" or bool(p.integrate_depth)
        if p.mode == "markerless" and not p.integrate_depth:
            log.info("integrate_depth=False has no effect in markerless mode (the fused model is the tracking reference)")
        self.depth_estimator = DepthEstimator(self.K, self.baseline, self.depth_params) if self._need_depth else None
        self.cpu_defaults_full = s_eff >= CPU_DEFAULTS_MIN_SCALE  # "The CPU path's defaults" (module docstring)

        planes: dict[str, LaserPlane] = {}
        if p.use_lasers:
            planes = {n: pl for n, pl in (calib.lasers or {}).items() if is_line_name(n)}
            if not planes:
                log.info("no calibrated laser planes: laser triangulation is skipped")
        self.planes = planes
        # as many lines of a family in the image as the calibration has planes of it (four lasers: 2)
        per_family = lines_per_family(planes)
        if per_family != self.laser_params.lines_per_family:
            self.laser_params = replace(self.laser_params, lines_per_family=per_family)
        if per_family > 1:
            compiled_link_rows()  # numba's start (about a second; the first time ever it compiles) is paid here, not by a frame

        # reconstruction TSDF (raw depth) and, in markerless mode, the tracking TSDF (filtered depth).
        # The confidence TSDF only reconstructs: with track_filter_px=0 the model also tracks, so Open3D.
        self.markerless: MarkerlessTracker | ConfidenceTsdf | None = None
        self.has_confidence = False
        # the gap guard of the reconstruction, in px of the processed image
        self.gap_guard_px = (GUARD_PX_1280 * self.image_size[0] / 1280.0 if p.gap_guard_px is None
                             else float(p.gap_guard_px))
        # the guard by the matcher's doubt (the confidence TSDF only); None: off
        self.gap_guard_sigma_mm = (float(p.gap_guard_sigma_mm) if p.gap_guard_sigma_mm is not None
                                   and float(p.gap_guard_sigma_mm) > 0 else None)
        self.gap_guard_sigma_px = (SIGMA_GUARD_PX_1280 * self.image_size[0] / 1280.0 if p.gap_guard_sigma_px is None
                                   else float(p.gap_guard_sigma_px))
        self._guard_in_fuse = False  # True: the Open3D reconstruction, which gets its depth guarded here
        # how deep behind a surface the mesh model takes an observation in full (mm); None: the whole truncation
        self.thin_walls_mm: float | None = None
        # the mesh model's cut where a voxel is seen from opposite sides (mm); None: not used
        self.opposite_mm: float | None = None
        # fine mesh detail on thin walls (ENGINE_PLAN X5 step 2, ohs.app.fine_walls): when set, every frame the
        # confidence model fuses is also handed to it (its depth, sigma, pose), for a second, finer pass; None: off
        self.fused_spool: Any = None
        if model is not None and not self._need_depth:
            raise ValueError("this pipeline fuses no depth (marker mode without integrate_depth): it takes no model")
        if self._need_depth:
            single = p.mode == "markerless" and p.tracking and self.track_filter_px <= 0
            if model is None:
                self.markerless = self._make_recon(p.recon, single_model=single)
            elif isinstance(model, ConfidenceTsdf):
                model.set_camera(self.K, self.image_size, gap_guard_px=self.gap_guard_px,
                                 sigma_guard_px=self.gap_guard_sigma_px)
                self.markerless = model
            else:
                model.set_camera(self.K, self.image_size)
                self.markerless = model
            # the confidence TSDF guards inside; a model that also tracks keeps every pixel (dropping
            # them for the tracker moved the trajectory)
            self._guard_in_fuse = (self.gap_guard_px > 0 and not single
                                   and not isinstance(self.markerless, ConfidenceTsdf))
            self.has_confidence = isinstance(self.markerless, ConfidenceTsdf)
            self.thin_walls_mm = self.markerless.back_mm if self.has_confidence else None
            self.opposite_mm = self.markerless.opposite_mm if self.has_confidence else None
            if self.gap_guard_sigma_mm is not None and not self.has_confidence:
                log.info("gap_guard_sigma_mm is set, but this mesh is made by Open3D's model (no GPU, or recon "
                         "\"open3d\"), which fuses no sigma: the guard by the matcher's doubt is not used")
            if p.thin_walls == "on" and self.thin_walls_mm is None:
                log.warning("thin_walls is \"on\", but this mesh is made by Open3D's model (no GPU, or recon "
                            "\"open3d\"), which has no short band: the mesh is as with \"off\"")
        self.tracker: MarkerlessTracker | None = None
        if p.mode == "markerless" and p.tracking:
            if self.track_filter_px > 0:
                self.tracker = MarkerlessTracker(self.K, self.image_size, self.track_fusion_params)
            else:
                self.tracker = self.markerless
        self._resolve_tracking_defaults()
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
        self._clip_rays: tuple[np.ndarray, np.ndarray] | None = None  # per-pixel ray grid of a clip plane, on first use
        self._track_filter_gpu: TrackingFilterGpu | None | bool = None  # None: not tried yet; False: CPU
        self.gpu_fallbacks: dict[str, str] = {}  # GPU stages that failed during the run (now on the CPU): stage -> reason
        self._filter_pending: tuple | None = None  # (depth, the filter started for it), see _start_tracking_input
        # the start of a scan tracks a second time on with_raw_depth (module docstring, "Detailed parts")
        self._probe: MarkerlessTracker | None = None
        self._probe_left = DETAIL_PROBE_FRAMES
        self._probe_res: TrackResult | None = None  # the detail probe's result for the frame just tracked
        # the scan's start (module docstring, "The scan's start"): the frames held back, None once it is over
        # (the GPU tracking model only: on the Open3D model the start (fit, first frame too) took an 8-frame test
        # recording 108 mm off, so without a GPU the setting is not used and the results are as before)
        self.start_frames = int(p.start_frames) if (p.mode == "markerless" and p.tracking and int(p.start_frames) > 1
                                                    and self.dual_model and self.model_backend == "gpu") else 0
        if int(p.start_frames) > 1 and not self.start_frames and self.dual_model:
            log.info("start_frames is set, but the tracking model is Open3D's (no GPU): the scan's start is not used")
        self._start: list[_Held] | None = [] if self.start_frames else None
        self._start_due = False
        self._held_now: _Held | None = None  # the frame being processed, when it is held back
        #: what the scan's start did (None: it was off or has not ended yet)
        self.start_report: dict | None = None
        #: detail mode on the CPU matcher's depth leaves its raw depth out this close (px) to a hole ("Detailed parts")
        hole = DETAIL_HOLE_PX_FLIP_1280 if self.depth_params.flip_average else DETAIL_HOLE_PX_1280
        self.detail_hole_px = int(round(hole * self.image_size[0] / NOMINAL_WIDTH))
        #: ... and this close (px) to the image border (PipelineParams.detail_border_px; 0: off)
        self.detail_border_px = int(round(float(p.detail_border_px or 0.0) * self.image_size[0] / NOMINAL_WIDTH))
        #: laser points dropped because they disagree with the frame's dense depth, per line
        self.laser_rejected: dict[str, int] = {}
        #: when a dict (set by the caller; None: not measured), each frame appends per line the signed
        #: distance of its laser points to the dense depth before the depth check (``laser_points``'s
        #: ``depth_gaps``, :func:`~ohs.vision.laser.depth_gap`): calibration health, FEATURE_PLAN H1
        self.laser_depth_gaps: dict[str, list[np.ndarray]] | None = None
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
    def process(self, frame: StereoFrame, pose: np.ndarray | None = None,
                clip_plane: tuple[np.ndarray, float] | None = None) -> FrameResult:
        """Process one raw stereo frame (``t_host`` = camera timestamp).

        ``pose`` (optional, 4x4 ``T_wc``) skips tracking and fuses the frame at that pose, e.g. to
        reprocess with poses known from elsewhere. ``clip_plane`` (optional, ``(n, d)`` in this
        frame's camera frame C, mm): only what lies in the half-space ``n . X + d > 0`` is kept;
        the depth elsewhere is set to 0 (no measurement) before anything is fused, and laser
        points elsewhere are dropped. It may instead be anything with a ``keep(P)`` method that
        says which camera-frame points ``P`` (..., 3) are kept (a scan's table map seen from this
        frame, :meth:`ohs.mesh.support.SupportMap.in_camera`). A merge uses it to leave out the
        table a part lay on.
        Raises ``ValueError`` if the raw image size does not match the calibration.
        """
        self._check_open()
        p = self.params
        t = float(frame.t_host)
        tim: dict[str, float] = {}
        extra: dict = {}
        n_fallbacks = len(self.gpu_fallbacks)
        t_start = time.perf_counter()
        self._held_now = None

        t0 = time.perf_counter()
        left_r, right_r = self.rectifier.rectify(frame.left, frame.right)
        tim["rectify"] = _ms(t0)

        # the laser lines are extracted in two CPU threads while the depth runs (on the GPU, or in
        # SGBM's own threads) and, when they take longer than it, while the frame is tracked
        futs = None
        if self.planes:
            if self._laser_pool is None:
                self._laser_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ohs-laser")
            futs = (self._laser_pool.submit(_timed, extract_laser_lines, left_r, self.laser_params),
                    self._laser_pool.submit(_timed, extract_laser_lines, right_r, self.laser_params)
                    if self.laser_params.stereo_check else None)

        depth = sigma = seen = None
        if self.depth_estimator is not None:
            t0 = time.perf_counter()
            if self.has_confidence:
                depth, sigma = self.depth_estimator.compute_with_sigma(left_r, right_r)
            else:
                depth = self.depth_estimator.compute(left_r, right_r)
            tim["depth"] = _ms(t0)
            if self.depth_estimator.gpu_failure is not None:
                self.gpu_fallbacks.setdefault("depth", self.depth_estimator.gpu_failure)
            if clip_plane is not None:
                # the depth as the camera saw it stays for what judges the frame's depth as a whole: the
                # fusion's gap guard (_fuse) and the laser points' depth check. What the clip leaves out
                # was measured, and is no patch the matcher could not match
                seen = depth
                depth = self._clip_depth(depth, clip_plane)
            # the tracking filter starts now and is collected when the frame is tracked
            # (tracking_input): track_filter is how long the frame then still waits for it.
            # (Not with a clip plane: the filter would start from the matcher's own, unclipped map.)
            elif self.dual_model and (pose is not None or p.mode == "markerless"):
                self._start_tracking_input(depth)

        # The lines are needed only for the laser points, and those need the depth but not the
        # pose. So the frame does not wait for the lines here (with four lasers their extraction
        # outlasts the depth): a laser thread collects them and triangulates the points while the
        # frame is tracked and fused. After tracking the frame waits for what is left of that:
        # laser_extract is the part of the wait until the lines are there, laser_triangulate the
        # rest (the points, and adding them to the cloud); laser_extract_cpu and
        # laser_triangulate_cpu are the work's own times
        fut_pts = None
        rejected: dict[str, int] = {}
        gaps: dict[str, list[np.ndarray]] | None = {} if self.laser_depth_gaps is not None else None
        if futs is not None:
            fut_pts = self._laser_pool.submit(_lines_then_points, futs, partial(
                laser_points, left_r, right_r, self.K, self.baseline, self.planes, self.laser_params,
                depth=depth if seen is None else seen, depth_sigma=sigma, rejected=rejected, depth_gaps=gaps))

        t0 = time.perf_counter()
        if pose is not None:
            T = np.asarray(pose, dtype=np.float64).reshape(4, 4)
            track = TrackResult(T.copy(), True, {"method": "external"})
            if self.dual_model and depth is not None:
                self.tracker.integrate(self.tracking_input(depth), None, T)
            self._fuse(depth, T, t, tim, sigma, seen=seen)
        elif p.mode == "markerless":
            if self.tracker is None:
                raise ValueError("this pipeline does not track (PipelineParams.tracking=False): pass the frame's pose")
            track = self._track_markerless(depth, t, tim, extra, sigma)
        else:
            track = self.markers.track(left_r, right_r, t)
            if track.ok:
                self._fuse(depth, track.T_wc, t, tim, sigma, seen=seen)
        tim["track"] = _ms(t0)
        self._t_prev = t

        pts: dict[str, np.ndarray] = {}
        lines = lines_r = None
        if fut_pts is not None:
            t0 = time.perf_counter()
            lines, lines_r, pts, tim["laser_extract_cpu"], tim["laser_triangulate_cpu"], t_lines = fut_pts.result()
            for name, n in rejected.items():
                self.laser_rejected[name] = self.laser_rejected.get(name, 0) + n
            if gaps and self.laser_depth_gaps is not None:
                for name, arrs in gaps.items():
                    self.laser_depth_gaps.setdefault(name, []).append(np.concatenate(arrs))
            if clip_plane is not None:
                pts = {name: self._clip_points(P, clip_plane) for name, P in pts.items()}
            if self._held_now is not None:  # the scan's start: added at the frame's pose when the start is over
                self._held_now.pts = pts
            elif track.ok:
                self.cloud.add_frame(pts, track.T_wc)  # all lines at once: the frame counts once
            wait = _ms(t0)
            tim["laser_extract"] = min(wait, max(0.0, 1e3 * (t_lines - t0)))
            tim["laser_triangulate"] = wait - tim["laser_extract"]
            tim["laser"] = tim["laser_extract"] + tim["laser_triangulate"]
        tim["total"] = _ms(t_start)
        if len(self.gpu_fallbacks) > n_fallbacks:  # a GPU stage failed on this frame: the rest runs on the CPU
            extra["gpu_fallback"] = dict(list(self.gpu_fallbacks.items())[n_fallbacks:])

        self._n_frames += 1
        self._n_ok += int(track.ok)
        self._trajectory.append((t, np.asarray(track.T_wc, dtype=np.float64).copy(), bool(track.ok)))
        self._held_now = None
        if self._start_due:
            self.finish_start()
        return FrameResult(int(frame.index), t, left_r, right_r, depth, lines, pts, track, tim, lines_r, extra, sigma)

    def _clip_depth(self, depth: np.ndarray, plane: Any) -> np.ndarray:
        """``depth`` with 0 (no measurement) wherever the pixel's point is not in the half-space
        ``n . X + d > 0`` (camera frame C), or, for a ``clip_plane`` with a ``keep`` method, wherever
        it is not kept."""
        if self._clip_rays is None:
            w, h = self.image_size
            v, u = np.mgrid[0:h, 0:w].astype(np.float32)
            y = (v - self.K[1, 2]) / self.K[1, 1]
            x = (u - self.K[0, 2] - self.K[0, 1] * y) / self.K[0, 0]
            self._clip_rays = (x, y)  # the point of a pixel is depth * (x, y, 1)
        x, y = self._clip_rays
        if callable(getattr(plane, "keep", None)):  # a map: what to keep, point by point
            d = depth.astype(np.float32, copy=False)
            keep = plane.keep(np.stack([d * x, d * y, d], axis=-1))
            return np.where(keep, depth, np.float32(0.0)).astype(depth.dtype, copy=False)
        n = np.asarray(plane[0], dtype=np.float32).reshape(3)
        keep = depth * (n[0] * x + n[1] * y + n[2]) + np.float32(plane[1]) > 0
        return np.where(keep, depth, np.float32(0.0)).astype(depth.dtype, copy=False)

    @staticmethod
    def _clip_points(P: np.ndarray, clip: Any) -> np.ndarray:
        """The laser points ``P`` (camera frame C) that ``clip`` keeps: as :meth:`_clip_depth` does."""
        if callable(getattr(clip, "keep", None)):
            return P[np.asarray(clip.keep(P), dtype=bool).reshape(len(P))]
        cn, cd = np.asarray(clip[0], dtype=np.float64).reshape(3), float(clip[1])
        return P[P @ cn + cd > 0]

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
                tw = self.params.thin_walls
                short = tw == "on" or (tw == "auto" and self.params.mode == "markerless")
                return ConfidenceTsdf(self.K, self.image_size, voxel_mm=fp.voxel_mm, trunc_mm=fp.trunc_mm,
                                    depth_min_mm=fp.depth_min_mm, depth_max_mm=fp.depth_max_mm,
                                    backend="gpu" if want == "auto" else "auto",
                                    hole_factor=(sm.hole_amp, sm.hole_r_px), gap_guard_px=self.gap_guard_px,
                                    sigma_guard_mm=self.gap_guard_sigma_mm, sigma_guard_px=self.gap_guard_sigma_px,
                                    sigma_guard_angle=bool(self.params.gap_guard_sigma_angle),
                                    back_mm=BACK_VOXELS * fp.voxel_mm if short else None,
                                    opposite_mm=(THIN_WALLS_OPPOSITE_VOXELS * fp.voxel_mm
                                                 if short and THIN_WALLS_OPPOSITE_VOXELS else None))
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
              sigma: np.ndarray | None = None, seen: np.ndarray | None = None, pos: int | None = None) -> None:
        """Integrate the raw depth (and its sigma, for the confidence TSDF) into the reconstruction TSDF at ``T_wc``.
        ``seen``: the depth before a ``clip_plane`` left some of it out (:meth:`process`): the gap guard is
        worked out on it, since what the clip left out was measured, and is no patch without depth. ``pos``: the
        frame's place in the trajectory (None: the frame being processed), for :attr:`fused_spool`."""
        if self.markerless is None or depth is None:
            return
        ti = time.perf_counter()
        if self._guard_in_fuse:
            fp = self.fusion_params
            g = depth if seen is None else seen
            ok = (g >= fp.depth_min_mm) & (g <= fp.depth_max_mm)  # the fused range; NaN compares false
            drop = gap_guard_mask(np.where(ok, g, np.float32(0.0)), self.gap_guard_px)
            if drop.any():
                depth = np.where(drop, np.float32(0.0), depth)
        if seen is not None and self.has_confidence:
            self.markerless.integrate(depth, sigma, T_wc, t=t, seen=seen)
        else:
            self.markerless.integrate(depth, sigma if self.has_confidence else None, T_wc, t=t)
        tim["integrate"] = _ms(ti)
        if self.fused_spool is not None and self.has_confidence:
            ts = time.perf_counter()
            self.fused_spool.add(len(self._trajectory) if pos is None else int(pos), depth, sigma, seen, T_wc)
            tim["keep_depth"] = _ms(ts)

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
            if self._start is not None:  # the scan's start: tracked, held back (module docstring)
                res = self._start_track(d_track, depth, t, R_prior, sigma)
            elif self._probe_left > 0:
                res = self._probe_track(d_track, depth, t, R_prior)
            else:
                res = self.tracker.track(d_track, None, t, R_prior,
                                         alt_depth=lambda: with_raw_depth(d_track, self._detail_raw(depth)))
            if res.ok and self._held_now is None:
                self._fuse(depth, res.T_wc, t, tim, sigma)
        for k, v in res.info.get("timings_ms", {}).items():
            if k == "total":
                continue
            tim["track_integrate" if (k == "integrate" and self.dual_model) else k] = float(v)
        return res

    def _probe_track(self, d_track: np.ndarray, depth: np.ndarray, t: float, R_prior: np.ndarray | None,
                     alt: np.ndarray | None = None) -> TrackResult:
        """A frame at the start of a scan (module docstring, "Detailed parts"): tracked on the
        filtered depth alone, and by a second tracker on :func:`with_raw_depth` (``alt``, made here when None). If
        the filtered depth can't hold the pose (a detailed part), the second tracker, consistent from the first
        frame on, takes over for the whole scan; after ``DETAIL_PROBE_FRAMES`` it is dropped."""
        if self._probe is None:
            self._probe = MarkerlessTracker(self.K, self.image_size, self.track_fusion_params)
        res = self.tracker.track(d_track, None, t, R_prior)
        res_p = self._probe.track(with_raw_depth(d_track, self._detail_raw(depth)) if alt is None else alt,
                                  None, t, R_prior)
        self._probe_res = res_p
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

    # ------------------------------------------------------------------ the scan's start (module docstring)
    def _start_track(self, d_track: np.ndarray, depth: np.ndarray, t: float, R_prior: np.ndarray | None,
                     sigma: np.ndarray | None) -> TrackResult:
        """A frame of the scan's start: tracked as without the start (by the scan's tracker and, while it runs, the
        detail probe), then held back: :meth:`finish_start` fuses it."""
        alt = with_raw_depth(d_track, self._detail_raw(depth))
        held = self._start
        main = self.tracker
        probe = self._probe
        self._probe_res = None
        if self.params.start_detail == "window" and self._probe_left > 0:
            if self._probe is None:
                self._probe = probe = MarkerlessTracker(self.K, self.image_size, self.track_fusion_params)
            res = self.tracker.track(d_track, None, t, R_prior)
            self._probe_res = self._probe.track(alt, None, t, R_prior)
            self._probe_left -= 1
            if not res.ok and self._probe_res.ok:  # the pose shown; which tracker goes on is decided at the end
                res = self._probe_res
        elif self._probe_left > 0:
            res = self._probe_track(d_track, depth, t, R_prior, alt)
            probe = self._probe if self._probe is not None else self.tracker  # the probe, or it took over
        else:
            res = self.tracker.track(d_track, None, t, R_prior, alt_depth=alt)
        if not held and not res.ok:  # nothing tracked yet: the start begins with the first frame that is
            return res
        e = _Held(len(self._trajectory), float(t), d_track, alt, depth, sigma, R_prior is not None)
        e.res.append((main, main.last_result))
        if self._probe_res is not None:
            e.res.append((probe, self._probe_res))
        held.append(e)
        self._held_now = e
        res.info["start_window"] = len(held)
        if len(held) >= self.start_frames or not res.ok:
            self._start_due = True  # at the end of this frame, when its laser points are in
        return res

    def finish_start(self) -> None:
        """End the scan's start (module docstring, "The scan's start"): register its frames again, make the
        tracking model again from them, fuse them and add their laser points. Does nothing when there is no start
        or it is over; called by the pipeline after the start's last frame and by everything that reads the whole
        scan (:meth:`mesh_with_sigma`, :meth:`points`, :meth:`laser_cloud`, :meth:`trajectory`)."""
        held, self._start, self._start_due = self._start, None, False
        if not held or self.tracker is None:
            return
        t0 = time.perf_counter()
        p = self.params
        rep: dict[str, Any] = {"frames": len(held), "detail": p.start_detail}
        if p.start_detail == "window" and self._probe is not None:
            main = [e.of(self.tracker) for e in held[1:]]
            prb = [e.of(self._probe) for e in held[1:]]
            ts = [float(r.info.get("trans_strength", 0.0)) for r in main if r is not None and r.ok]
            lost = any((r is None or not r.ok) and q is not None and q.ok for r, q in zip(main, prb))
            weak = lost or (bool(ts) and float(np.median(ts)) < DETAIL_PROBE_STRENGTH)
            rep.update(median_strength=float(np.median(ts)) if ts else None, detail_mode=bool(weak))
            if weak:
                log.info("the filtered depth can't hold the pose over the scan's first %d frames (a detailed part): "
                         "this scan tracks with the raw depth where the filter drops it", len(held))
                self.tracker.close()
                self.tracker, self._probe = self._probe, None
                self.tracker.detail_mode, self.tracker.detail_frame = True, len(held) - 1
            self._end_probe()
        tr = self.tracker
        if p.start_detail_only and not tr.detail_mode:
            # a scan whose filtered depth holds its translation (a smooth part): the frames are fused at their poses,
            # and the tracker goes on as it is
            rep["registered"] = 0
            for e in held:
                r = e.of(tr)
                if r is not None and r.ok:
                    self._fuse(e.depth, r.T_wc, e.t, {}, e.sigma, pos=e.pos)
                    self.cloud.add_frame(e.pts, r.T_wc)
            rep["ms"] = round(_ms(t0), 1)
            self.start_report = rep
            return
        rows = [(e, e.of(tr)) for e in held]
        ok = [(e, r) for e, r in rows if r is not None and r.ok]
        inputs = [e.alt if tr.detail_mode else e.d_track for e, _ in ok]
        poses = [np.asarray(r.T_wc, dtype=np.float64).copy() for _, r in ok]
        times = [e.t for e, _ in ok]
        first = [T.copy() for T in poses]
        if len(ok) > 2 and int(p.start_sweeps) > 0:
            poses = self._refine_start(tr, inputs, poses, times, [e.gyro for e, _ in ok])
        if ok:
            n_lost, R_acc = tr._n_lost, tr._R_acc.copy()
            cover = getattr(tr, "_uncovered", 0), getattr(tr, "_uncovered_s", 0.0)  # (a stand-in may not have them)
            tr.restart(list(zip(inputs, poses, times)))
            if rows[-1][1] is None or not rows[-1][1].ok:  # the start ended at a frame that failed: still lost
                tr._n_lost, tr._R_acc = max(n_lost, 1), R_acc
                tr._uncovered, tr._uncovered_s = cover  # and the gyro cover since the last good pose (ENGINE_PLAN R2-4)
        T_prev = np.eye(4)
        done = {id(e): T for (e, _), T in zip(ok, poses)}
        for e in held:
            T = done.get(id(e))
            if T is None:
                self._trajectory[e.pos] = (e.t, T_prev.copy(), False)
                continue
            self._fuse(e.depth, T, e.t, {}, e.sigma, pos=e.pos)
            self.cloud.add_frame(e.pts, T)
            self._trajectory[e.pos] = (e.t, T.copy(), True)
            T_prev = T
        self._n_ok = sum(1 for _, _, k in self._trajectory if k)
        moved = [float(np.linalg.norm(a[:3, 3] - b[:3, 3])) for a, b in zip(first, poses)]
        rep.update(registered=len(ok), moved_mm=[round(m, 3) for m in moved], ms=round(_ms(t0), 1))
        self.start_report = rep

    def _refine_start(self, tr: MarkerlessTracker, inputs: list[np.ndarray], poses: list[np.ndarray],
                      times: list[float], gyro: list[bool]) -> list[np.ndarray]:
        """The start's frames registered again (module docstring, "The scan's start"): in each of ``start_sweeps``
        rounds every frame but the first (the world's frame) against a model of all the others at their poses, from
        its own pose or (``start_predict`` "fit") from a fit in time of the others' positions; one after the other, or
        with ``start_together`` all against the poses from before the round."""
        P = [T.copy() for T in poses]
        n = len(P)
        j0 = 0 if self.params.start_first else 1
        for _ in range(int(self.params.start_sweeps)):
            Q = [T.copy() for T in P] if self.params.start_together else P  # what this round registers against
            for j in range(j0, n):
                tr.rebuild([(inputs[i], Q[i]) for i in range(n) if i != j])
                start = Q[j] if self.params.start_predict == "pose" else _fit_start(Q, times, j)
                T, _ = tr.register(inputs[j], start, gyro=gyro[j] if j else True)
                if T is not None:
                    P[j] = T
            if j0 == 0:  # the world stays the first frame's camera
                A = np.linalg.inv(P[0])
                P = [A @ T for T in P]
        return P

    def _resolve_tracking_defaults(self) -> None:
        """The CPU path's defaults (module docstring): fill in ``DepthParams.flip_average`` and the tracking
        model's ``velocity_max_accel`` and ``raycast_weight_threshold`` where they are left ``None``. On the CPU
        path (CPU matcher, Open3D tracking model, separate tracking model) the guard is on and, at full
        resolution, the flip average and the weight threshold 8; everywhere else what they were before (no
        flip, no guard, 3). The reconstruction model's parameters (``fusion_params``, what a report shows) get
        the same values; it only integrates there, so they change nothing in it."""
        def resolved(fp: FusionParams, cpu: bool) -> FusionParams:
            va = fp.velocity_max_accel if fp.velocity_max_accel is not None else (VELOCITY_MAX_ACCEL if cpu else None)
            wt = (fp.raycast_weight_threshold if fp.raycast_weight_threshold is not None
                  else CPU_RAYCAST_WEIGHT_THRESHOLD if cpu and self.cpu_defaults_full else RAYCAST_WEIGHT_THRESHOLD)
            return replace(fp, velocity_max_accel=va, raycast_weight_threshold=wt)

        de = self.depth_estimator
        cpu = (self.dual_model and getattr(self.tracker, "model_backend", None) == "open3d"
               and de is not None and de.backend == "cpu")
        if self.depth_params.flip_average is None:
            self.depth_params = replace(self.depth_params, flip_average=cpu and self.cpu_defaults_full)
            if de is not None:
                de.params = self.depth_params
        self.track_fusion_params = resolved(self.track_fusion_params, cpu)
        ttm = self.params.track_trunc_mult  # the Open3D tracking model's own truncation (PipelineParams)
        if ttm is not None and self.dual_model and getattr(self.tracker, "model_backend", None) == "open3d":
            self.track_fusion_params = replace(self.track_fusion_params, trunc_mult=float(ttm))
            self.tracker._trunc_mm = float(self.track_fusion_params.trunc_mm)  # what MarkerlessTracker.trunc_mm says
        self.fusion_params = resolved(self.fusion_params, cpu)
        if self.tracker is not None:
            self.tracker.params = self.track_fusion_params if self.dual_model else self.fusion_params
        if self.markerless is not None and self.markerless is not self.tracker and not self.has_confidence:
            self.markerless.params = self.fusion_params  # the Open3D reconstruction

    def _detail_raw(self, depth: np.ndarray) -> np.ndarray:
        """The raw depth detail mode adds where the filter dropped a pixel: ``depth`` itself from the GPU
        matcher, without its hole borders (:func:`without_hole_borders`, :attr:`detail_hole_px`) from the
        CPU matcher (module docstring, "Detailed parts")."""
        est = self.depth_estimator
        if est is None or est.backend != "cpu":
            return depth
        out = without_hole_borders(depth, self.detail_hole_px)
        b = self.detail_border_px
        if b > 0:
            out = out.copy() if out is depth else out
            out[:b], out[-b:], out[:, :b], out[:, -b:] = 0.0, 0.0, 0.0, 0.0
        return out

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

    def mesh_with_sigma(self, with_looks: bool = False) -> tuple:
        """:meth:`mesh` plus each vertex's standard deviation (mm, float32), or ``None`` when the
        reconstruction is Open3D's (no confidence); with ``with_looks`` also each vertex's count of
        independent looks (float32, :meth:`ConfidenceTsdf.extract_mesh`), ``None`` the same way."""
        self.finish_start()
        if not self.has_confidence:
            V, F, N = self.mesh()
            return (V, F, N, None, None) if with_looks else (V, F, N, None)
        self._check_open()
        empty = (np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros((0, 3)), np.zeros(0, np.float32))
        if with_looks:
            empty += (np.zeros(0, np.float32),)
        if self.markerless.n_integrated == 0:
            return empty
        try:
            return self.markerless.extract_mesh(with_sigma=True, with_looks=with_looks)
        except RuntimeError as exc:
            log.warning("mesh extraction failed (%s); returning an empty mesh", exc)
            return empty

    def points(self) -> tuple[np.ndarray, np.ndarray]:
        """TSDF zero-crossing points and normals ``(P, N)`` (world, mm), e.g. for Poisson meshing."""
        self._check_open()
        self.finish_start()
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
        if not self._closed:
            self.finish_start()
        return self.cloud

    def release_model(self) -> MarkerlessTracker | ConfidenceTsdf | None:
        """Hand over the reconstruction model (for another pipeline's ``model``): this pipeline
        no longer has one, and :meth:`close` leaves it alone."""
        self._check_open()
        self.finish_start()
        m, self.markerless = self.markerless, None
        if self.tracker is m:
            self.tracker = None
        return m

    def trajectory(self) -> list[tuple[float, np.ndarray, bool]]:
        """``[(t, T_wc, ok), ...]`` for every processed frame (failed frames carry the last good pose). The poses of
        the scan's start are those it ended with (module docstring, "The scan's start")."""
        if not self._closed:
            self.finish_start()
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
        self._start = [] if self.start_frames else None
        self._start_due = False
        self._held_now = None
        self.start_report = None
        if self.markers is not None:
            self.markers.reset()
        self.cloud.clear()
        self.laser_rejected = {}
        if self.laser_depth_gaps is not None:
            self.laser_depth_gaps = {}
        self._trajectory = []
        self._t_prev = None
        self._n_frames = 0
        self._n_ok = 0

    def close(self) -> None:
        """Release the voxel grids and trackers. The pipeline cannot be used afterwards."""
        self._closed = True
        self._start = None  # frames still held back are dropped
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
