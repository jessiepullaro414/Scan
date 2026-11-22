"""Markerless frame-to-model tracking with TSDF fusion (KinectFusion style, Open3D tensor TSDF or a GPU volume).

Per frame (:meth:`MarkerlessTracker.track`):

1. **Prediction.** ``T_pred = T_last @ [R_rel | t_rel]``: ``R_rel`` is the gyro prior ``R_prior``
   (rotation about the camera centre; ``v_prev = R_prior @ v_cur``, see
   :class:`ohs.track.imu_prior.GyroPredictor`), else the constant-velocity rotation, else ``I``;
   ``t_rel`` is the constant-velocity translation (``FusionParams.constant_velocity``).
2. **Model view.** The TSDF is ray cast at ``T_pred`` (Open3D ``VoxelBlockGrid.ray_cast``, at
   ``raycast_scale`` of the input resolution). Open3D marches with nearest-voxel TSDF lookups,
   which leaves a ~half-voxel bias and a staircase (p90 1.3 mm on a plane at 2 mm voxels), so
   every hit is moved onto the zero crossing of the *trilinear* TSDF by an exact Newton step
   (corner values and weights come from the ray cast's ``index``/``interp_ratio`` outputs, the
   gradient along the ray is computed analytically). A cheaper projective step (``dTSDF/dt``
   assumed -1/trunc) left a view-dependent bias of ~0.05 deg per frame. Normals are the TSDF
   gradients, box-averaged over ``model_normal_window`` pixels: raw gradients of a young model
   are so noisy (~18 deg after one frame at 1 mm voxels) that point-to-plane ICP barely moves
   along sliding directions.
3. **Registration.** Multi-scale projective point-to-plane Gauss-Newton ICP (numpy): source =
   current depth back-projected at strides ``icp_strides`` (with normals for a compatibility
   test), target = the refined model maps; distance and normal gates, Huber weights, a rotation
   prior (strong ``gyro_sigma_deg`` when ``R_prior`` is given, weak otherwise) and a weak
   translation prior. The priors make geometrically degenerate scenes (a sphere on a plane is
   symmetric about its vertical axis; a flat panel lets the scanner slide) well-posed:
   unobservable directions stay at the prediction, which carries the gyro.
   ``info["degenerate"]`` flags such geometry (heuristic, see ``degenerate_eig_ratio``), and
   ``info["weak"]`` says which motions it allows (see "Weak directions" below).
4. **Gates.** Enough valid depth and model pixels, fitness (inlier fraction at the finest
   level), point-to-plane inlier RMSE, and the jump from the prediction (``max_jump_mm`` /
   ``max_jump_deg``).
5. **Fusion.** On success the frame is integrated into the TSDF.

On failure the result has ``ok=False``, ``T_wc`` = the last good pose and ``info["reason"]`` one
of ``no_depth`` (too few valid pixels), ``bad_depth`` (no locally smooth surface), ``model_not_visible``,
``diverged``, ``few_inliers``, ``low_fitness``, ``high_rmse``, ``jump``; nothing is integrated.
The following frames relocalise from the last good pose rotated by the gyro rotation
accumulated since then (``R_prior`` of every call is chained; without a gyro: the plain last
good pose). The coarse correspondence distances and the jump gates are widened by
``1 + n_lost`` (at most ``max_widen``) and an extra coarse level is added; the finest level is
never widened, so the acceptance gates keep their meaning.

**Weak directions** (``info["weak"]``). The eigen-directions of the final ICP information below
``degenerate_eig_ratio`` of the strongest (rotations scaled to mm at the scene distance) are the
motions the surface can't pin down. Their span is classified, since weak eigenvectors mix
freely:
- ``"slide"`` when it holds a translation (a flat area);
- ``"turn"`` when it holds a rotation about any axis (a sphere's axis, a flat area's normal).

``axis`` is that direction in the camera frame. The soft priors hold these directions at the
prediction (gyro, constant velocity), blended with whatever weak information the data has. The
live app turns them into hints: sliding even with the gyro, turning only without it.

**Detailed parts** (``track(alt_depth=...)``). The pipeline tracks on filtered depth (see
:func:`ohs.app.pipeline.tracking_depth`), which suppresses the dots' camera-fixed error on smooth
surfaces by eroding around every depth step. A detailed part (a carburetor, a machined bracket) is
all steps, so the filter erases most of it: what is left, often the bench it stands on, can't hold
the scanner's sideways motion, and the pose slides away. Each frame's final ICP therefore measures
how well its input holds the weakest translation while the gyro holds the rotation
(:func:`_translation_strength`, ``info["trans_strength"]``). On the simulator the filtered depth
of smooth parts holds 1e-3 to 1e-2, and that of detailed parts 1e-5 to 4e-5. Such a scan is
tracked on ``alt_depth``, the filtered depth plus the raw depth wherever the filter dropped a
pixel, from its first frame (:attr:`MarkerlessTracker.detail_mode`): the pipeline finds out with a
second tracker over the first frames (``ohs.app.pipeline``, "Detailed parts"). Retrying single
weak frames with ``alt_depth`` did not work: the part enters the model at poses that already
slid, the filter's leftover fragments of it then look well held but are biased, and on the door
trim it only made the worst frames worse. A frame that fails a gate still tries ``alt_depth``
before it is lost (``VALIDATION.md``, "Large and detailed parts").

**The velocity guard** (``FusionParams.velocity_max_accel``, Open3D model; off in this class by
default, on in the processing pipeline's CPU path since 2026-11-20: :mod:`ohs.app.pipeline`, "The CPU
path's defaults"; on the GPU model only with ``FusionParams.gpu_velocity_guard``, off by default, ENGINE_PLAN
X4). On a
scene the depth barely holds sideways (a dark part a few percent of the image, the rest a bench), one
frame can be pushed far off, and the constant-velocity prediction then carries the error on: the
directions the data cannot hold stay at the prediction, so a wrong velocity repeats every frame. With the
guard, the velocity a frame carries into the next prediction (not its pose, which is what ICP found)
differs from the last one by at most ``velocity_max_accel`` times the frame time, and the first one after
the start or a lost frame is at most :data:`VELOCITY_MAX_SPEED`. The bounds come from the simulator's hand
(at most 0.48 m/s^2 and 119 mm/s over the F14 recordings) with margins of 4 and 2.5
(:data:`VELOCITY_MAX_ACCEL`, 2 m/s^2: 2.2 mm a frame at 30 frames a second). A frame it acted on says
so: ``info["velocity_guard_mm"]``, how much of its velocity (mm a frame) was not carried on. The ICP's own
information was no guide: the frame that started the dark bracket's slide held its weakest translation
at 5e-3, ten times the detail-mode threshold (``MAINTENANCE_PLAN.md`` F14).

**A weakly held step** (``FusionParams.weak_step_strength``, ``weak_step_mm``, ``weak_step_mode``; ENGINE_PLAN
X4b (c); both models; off unless set). On the GPU path the hole plate 120 mm above its background lost tracking
at frame 52: one frame landed 4.2 mm off its truth from a start 1.7 mm off while the tracker's own numbers looked
healthy, and the constant-velocity prediction carried the jump on (8 and 23 mm off at the next two starts). Its
translation was weakly held (``trans_strength`` 3.6e-4). With the setting, a frame that holds its weakest
translation (``info["trans_axis"]``, the smallest eigenvector of the ICP information's translation block) less
than ``weak_step_strength``, and whose step along that direction differs from the last carried step by more than
``weak_step_mm``, carries that direction's last step instead ("hold") or also takes its own jump there back out of
the next prediction ("undo"). Its pose is what ICP found; ``info["weak_step_mm"]`` says how far the carried step
moved.

**A frame that slides into another fit** (``FusionParams.slip_check_mm``, ``slip_starts``, ``slip_margin``; ENGINE_PLAN
X4c; both models; off unless set). The hole plate's frame 52 above, measured against the truth before it was fused:
its prediction was 1.6 mm off, and ICP slid 6.7 mm from it along the weakly held direction (along the table) and
stopped 5.1 mm off. Started from the true pose the same ICP stays there (0.35 mm), and so does every other start
tried (the last pose without the carried step, the prediction without the coarsest level: 0.2 to 0.9 mm). The
model agrees better with the frame there: the truncated point-to-plane cost (:meth:`MarkerlessTracker._agreement`,
which also counts the frame's points that find no model point) is 2.97 against 3.26 mm^2, where on the frames
around it the results of those starts lie within 0.5 mm of each other and their costs within 0.03. ICP's own
numbers did not show it (fitness 0.80 against 0.81, rmse as usual): its cost leaves out the points it loses, and the
slide lost 2 % of them. With the setting, a frame whose step along its weakest translation (``info["trans_axis"]``)
leaves the prediction by more than ``slip_check_mm`` is registered again from ``slip_starts`` ("rest": the last pose
turned by the gyro, without the carried step; "fine": the prediction without the coarsest ICP level) and keeps the
result with the lowest cost when it beats the first by more than ``slip_margin``; ``info["slip_check"]`` says what
was compared and kept. The frames that did not slide that far are tracked exactly as without it. Measured on the
frame pass (2026-11-22, ``slip_check_mm`` 3): the hole plate's slip is held with and without the scan's start, at
every start length tried (path max after the start 2.3 to 3.1 mm, from 16.3 and up to 19.1). It does not mend
bracket seed 1's frames 151 to 166 (never acts there): those land 1 to 1.6 mm off because the model, fused at
poses a few tenths of a mm off, pulls them (against a model of the same frames at true poses they land 0.1 mm off),
which no second start can mend. Not adopted (ENGINE_PLAN X4c).

**The newest frame held out of the model** (``FusionParams.fuse_lag``, ``fuse_lag_detail_only``; ENGINE_PLAN X4c; both
models; off unless set). Measured on bracket seed 1, frames 100 to 200, against the truth before fusion: each frame
lands off along its weakest translation only (``info["trans_axis"]``), with no shared bias (mean 0.01 mm) and the sign
alternating from frame to frame (lag-one correlation -0.3 to -0.5), 0.45 mm rms where the translation is held at 6e-4
to 9e-4 and 0.19 mm on bracket seed 0 (1.2e-3 to 1.7e-3). A model of the same frames at their true poses does not
make that smaller (0.46 against 0.44 mm rms over frames 110 to 172), a shorter memory makes it larger (the last 5 /
10 / 30 / 60 frames: 0.69 / 0.61 / 0.55 / 0.46), but the same model without the newest frame makes it 0.38 (largest
1.01 against 1.63): where new surface comes into view the model there is mostly that frame, so the next frame
registers against its error and keeps it (frame 152 landed 1.63 mm off, and the path stayed about 1.3 mm off for the
next 15 frames). With the setting a tracked frame is fused only once the next ``fuse_lag`` frames have been
registered; its pose is what ICP found, and ``info["fuse_lag"]`` says how many frames are held. What is held is fused
when the model is read out (:meth:`extract_mesh`, :meth:`extract_points`) or a pose is given (:meth:`integrate`),
and :meth:`rebuild` forgets it. With ``fuse_lag_detail_only`` it acts only in detail mode, so a smooth part is
tracked exactly as before. Measured 2026-11-22 (``fuse_lag`` 1, with ``slip_check_mm`` 3): bracket seed 1's path max
stays at 1.77 to 2.30 mm for every start tried (2.40 today, 1.81 to 3.30 without it), the hole plate's at 2.4 to
3.2 mm; but on the small set of what Make mesh runs, other detail-mode scenes move both ways (the speckled bracket,
the hole plates' small features), so it is not adopted (ENGINE_PLAN X4c). Two frames (``fuse_lag`` 2) were worse.

**The pull along a table** (ENGINE_PLAN X0a; Open3D model only, each off unless set). Without a GPU the camera
path slid along the table on every simulated scene: started at the true pose of a frame, ICP against the Open3D
model moved the camera 3.4 to 6.6 mm along it (the GPU model: 0.4 to 1.2 mm), even a frame against its own
one-frame model 0.65 mm (GPU 0.05). Three settings act on it:

* ``FusionParams.model_normals = "vertex_map"``: the model's normals from its refined vertex map (central
  differences, none across a depth step over :data:`VERTEX_MAP_STEP_MM`) instead of the TSDF's gradients. At
  2 mm voxels a 0.5 mm step fuses into a ramp several voxels wide whose gradient normals tilt the other way
  than its vertices; on exact depth of a 0.5 mm plate a frame against its own model moved 8 mm with them and
  0.5 mm with the vertex map's.
* ``FusionParams.raycast_border_mm``: the GPU model's border rule (``ohs.track.volume_gpu``, rule (iv)) on the
  Open3D ray cast, with nearest-voxel lookups: a hit is kept only when :data:`BORDER_PROBES` probes on a circle
  of that radius around it, in its tangent plane, lie in fused voxels.
* ``FusionParams.velocity_from_rest``: with the velocity guard, the first velocity after the start or a lost
  frame is bounded from rest (``velocity_max_accel`` times the frame time, 2.2 mm a frame at 30 frames a
  second) instead of by :data:`VELOCITY_MAX_SPEED`.

Two stronger treatments from the literature made tracking worse on the simulator
(VALIDATION.md, "Degeneracy-aware tracking"), so neither is used:
- remapping the update so that ICP never moves along weak directions (Zhang, Kaess and Singh
  2016) throws away the little, correct information a crease or a hole edge gives;
- geometrically stable sampling (Gelfand et al. 2003) favours grazing points, whose stereo depth
  is the least reliable. It may pay off once points carry a depth confidence.

**Model backends** (``FusionParams.model_backend``, :attr:`MarkerlessTracker.model_backend`):

* ``"open3d"``: the sparse Open3D ``VoxelBlockGrid`` above (CPU, or CUDA via ``device``). It
  keeps the whole scan, so it is the one to mesh (``extract_mesh`` / ``extract_points``).
* ``"gpu"``: :class:`ohs.track.volume_gpu.GpuTsdfVolume`, a dense *rolling* 2 mm-class cube that
  follows the scanner, integrated and ray cast in OpenCL on any GPU (``ohs.compute``). Its ray
  cast already lands on the trilinear zero crossing and box-averages the normals on the device,
  so it returns the same model maps as the Open3D path (same frames and units); ICP is
  unchanged. Its hits must lie ``gpu_border_mm`` inside the observed surface and their voxels
  must have been fused ``gpu_weight_threshold`` times (capped by the frame count): the
  borders of what was observed carry camera-fixed stereo errors, which Open3D's nearest-voxel
  ray cast happens to skip and which otherwise pull ICP (see the volume's module docstring).
  It is meant for the *tracking* model: surfaces that left the cube are
  forgotten. ``extract_mesh`` / ``extract_points`` still work (they copy the observed bricks
  into a CPU Open3D grid), but only for what is inside the cube. With this backend the ray cast
  is queued before the source points are computed, so the two overlap, and integration is
  asynchronous. It may truncate closer than the Open3D model (``FusionParams.gpu_trunc_mult``;
  the pipeline's tracking model: 2 voxels, 4 mm): a wide band swells edges and thin walls, and
  on stereo depth this model tracks as well with the short one, which the Open3D model does not
  (``VALIDATION.md``, "Where a tracked scan's shape errors come from").
* ``"auto"`` (default): ``"gpu"`` when :func:`ohs.compute.get_device` gives a GPU (and the volume
  can be created there), else ``"open3d"``. ``"gpu"`` raises when no GPU is available.

Open3D notes (0.20, verified empirically):

* everything is in **mm**: ``depth_scale=1``, ``voxel_size`` and depth limits in mm; extrinsics
  are ``T_cw`` in mm;
* ``integrate`` truncates the projected pixel coordinate; the integration intrinsics are shifted
  by +0.5 px so the lookup becomes round-to-nearest (removes a ~0.35 mm bias on tilted surfaces);
* ``ray_cast`` must never need as many "fragments" as its buffer holds: it then leaves blocks out
  of its range map and reads entries nobody filled in, which crashes or hangs now and then
  (:meth:`MarkerlessTracker._reserve_fragments` keeps the buffer larger than any cast can need);
* ``ray_cast`` hangs when ``range_map_down_factor`` does not divide the image size (a divisor is
  chosen automatically);
* ray-cast ``vertex``/``normal`` are in the camera frame and normals point *away* from the camera
  (flipped here); ``hashmap().key_tensor()`` includes inactive slots (only active keys are used).

The public API is in mm; :meth:`extract_mesh` returns outward normals and counter-clockwise faces.
The ``intensity`` image is accepted for API symmetry but not used: the projected dot pattern moves
with the scanner, so photometric alignment on these images would bias the motion towards zero.
"""
from __future__ import annotations

import contextlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import numpy as np

from ohs.geometry import invert_se3, make_se3, rotation_angle_deg, skew, so3_exp, so3_log
from ohs.types import TrackResult

__all__ = ["FusionParams", "MarkerlessTracker", "MODEL_BACKENDS"]

log = logging.getLogger(__name__)

MODEL_BACKENDS = ("auto", "open3d", "gpu")
MODEL_NORMALS = (None, "gradient", "vertex_map")  # FusionParams.model_normals (module docstring, "The pull along a table")
WEAK_STEP_MODES = ("hold", "undo")  # FusionParams.weak_step_mode (module docstring, "A weakly held step")
SLIP_STARTS = ("rest", "fine")  # FusionParams.slip_starts (module docstring, "A frame that slides into another fit")
O3D_THREADS_ENV = "OHS_O3D_THREADS"  # see _limit_open3d_threads


@contextlib.contextmanager
def open3d_threads(n: int) -> Iterator[None]:
    """Hold Open3D to ``n`` threads inside the block (the limit is process-wide while it lasts) and
    put back what was there before. For one repeatable run on the Open3D model, see
    :func:`_limit_open3d_threads`."""
    import open3d as o3d  # noqa: PLC0415 (optional heavy dependency)

    if not hasattr(o3d.utility, "set_max_threads"):  # pragma: no cover - older Open3D
        log.warning("this Open3D (%s) cannot limit its threads", o3d.__version__)
        yield
        return
    before = int(o3d.utility.get_max_threads())
    o3d.utility.set_max_threads(int(n))
    try:
        yield
    finally:
        o3d.utility.set_max_threads(before)


def _limit_open3d_threads(o3d: Any) -> None:
    """Apply ``OHS_O3D_THREADS`` (a whole number; unset or empty: leave Open3D alone; 0: Open3D's
    own choice) as Open3D's process-wide thread limit.

    Why: on several threads Open3D's ``VoxelBlockGrid.ray_cast`` (0.20, CPU) gave slightly
    different answers for the same grid and pose (a few of some 25,000 surface pixels came and
    went, points moved by up to 2 mm), and its hash map hands the blocks out in a different order.
    So two runs of the same recording on the Open3D model differed a little: poses by up to 0.008
    in a matrix entry, the mesh by a few vertices. With ``OHS_O3D_THREADS=1`` they are the same
    bit for bit, at about half the speed (ray cast 43 -> 106 ms, integration 9 -> 55 ms a frame
    at 640 x 400). It is a switch for before/after comparisons, not the default
    (``tools/snapshot.py`` does the same on the CPU path with :func:`open3d_threads`, for the
    time it processes only). The limit stays for the life of the process. The GPU model does not
    use these calls and is repeatable as it is.

    Since 2026-11-19 the pixels that came and went have a known cause, and it is gone: the ray
    cast ran out of room for its fragments and left blocks out, which ones by the order of its
    threads (:meth:`MarkerlessTracker._reserve_fragments`). Runs on several threads have given
    the same poses and the same mesh vertices since (in another order in the file), so the
    switch is kept as a second safeguard only."""
    v = os.environ.get(O3D_THREADS_ENV, "").strip()
    if not v:
        return
    try:
        n = int(v)
    except ValueError:
        n = -1
    if n < 0:
        raise ValueError(f"{O3D_THREADS_ENV} must be a whole number of threads (0 = Open3D's own choice), got {v!r}")
    if not hasattr(o3d.utility, "set_max_threads"):  # pragma: no cover - older Open3D
        log.warning("%s is set, but this Open3D (%s) cannot limit its threads", O3D_THREADS_ENV, o3d.__version__)
        return
    o3d.utility.set_max_threads(n)
SLIDE_SV = 0.8  # the weak subspace holds a translation this close to a pure one: "slide"
TURN_SV = 0.5  # ... a rotation (about any axis, e.g. through the scene) at least this strongly: "turn"

_FRAGMENT_CELLS = 16  # Open3D's range map is filled from fragments of 16 x 16 of its cells
# the velocity guard's bound (m/s^2; module docstring, "The velocity guard"): the simulator's hand moves the
# scanner with at most 0.48 m/s^2 (frame to frame, over the recordings of MAINTENANCE_PLAN F14), times 4
VELOCITY_MAX_ACCEL = 2.0
RAYCAST_WEIGHT_THRESHOLD = 3.0  # the Open3D model's ray-cast weight threshold where FusionParams leaves it None
# ... and the first velocity after the start or a lost frame (mm/s): the simulator's hand moves at most 119 mm/s
# (4 mm a frame at 30 fps), times 2.5
VELOCITY_MAX_SPEED = 300.0
# FusionParams.model_normals = "vertex_map": no normal across a step in the vertex map's depth larger than this (mm);
# a 0.5 mm plate's edge is no such step (its normals there follow the fused ramp)
VERTEX_MAP_STEP_MM = 4.0
BORDER_PROBES = 8  # FusionParams.raycast_border_mm: probes on the circle, as the GPU model's rule (iv)

# trilinear cell corners in the order of Open3D's ray-cast "index"/"interp_ratio" outputs
_CORNERS = np.array([[j & 1, (j >> 1) & 1, (j >> 2) & 1] for j in range(8)], dtype=np.float64)


@dataclass
class FusionParams:
    """Settings for :class:`MarkerlessTracker` (all lengths in mm).

    Defaults target final quality (1 mm voxels); for live feedback at half resolution use
    ``voxel_mm=2.0``. ``block_count=None`` sizes the initial hash map for the surfaces inside a
    ~0.5 m cube; Open3D grows it automatically (doubling) when needed.
    """

    voxel_mm: float = 1.0
    trunc_mult: float = 4.0  # truncation distance = trunc_mult * voxel_mm
    depth_min_mm: float = 120.0
    depth_max_mm: float = 500.0
    device: str = "auto"  # Open3D device: "auto" | "cpu" | "cuda" | "CUDA:1" ...
    block_resolution: int = 16
    block_count: int | None = None
    # model backend (module docstring): "auto" = the GPU volume when ohs.compute finds a GPU
    model_backend: str = "auto"  # "auto" | "open3d" | "gpu"
    gpu_volume_voxels: int = 384  # edge of the GPU volume (voxels, multiple of 16): 768 mm at 2 mm
    gpu_border_mm: float = 8.0  # GPU ray cast: hits at least this far inside the observed surface
    gpu_weight_threshold: float = 8.0  # GPU ray cast: weight threshold (capped by the frame count)
    # the GPU model's truncation in voxels; None: trunc_mult. A TSDF writes "inside" as far as the truncation behind
    # every surface it sees, so edges and walls thinner than that swell once they are seen from the other side.
    # The pipeline's tracking model asks for 2 (ohs.app.pipeline.TRACK_GPU_TRUNC_MULT); the Open3D model keeps
    # trunc_mult: on stereo depth it tracks worse with a shorter band (MarkerlessTracker.trunc_mm says which holds).
    gpu_trunc_mult: float | None = None
    # the GPU model's band behind the surface (ohs.track.volume_gpu, "The band behind the surface"): a voxel that
    # was integrated gpu_back_after times takes nothing more from deeper than gpu_back_voxels voxels behind a surface,
    # at right angles to it. None: off. The pipeline's tracking model asks for 1 voxel
    # (ohs.app.pipeline.TRACK_GPU_BACK_VOXELS); the Open3D model has no such rule (MarkerlessTracker.back_mm).
    gpu_back_voxels: float | None = None
    gpu_back_after: int = 4
    # model ray casting
    raycast_scale: float = 0.5  # model maps rendered at this fraction of the input resolution
    # Open3D model: a hit's voxel must have been fused this often (capped by the number of integrated frames).
    # None: RAYCAST_WEIGHT_THRESHOLD (3) here; ohs.app.pipeline fills in 8 on the CPU path at full resolution
    # (MAINTENANCE_PLAN F14, the owner's decision D7 of 2026-11-20). The GPU model uses gpu_weight_threshold.
    raycast_weight_threshold: float | None = None
    model_normal_window: int = 5  # box-average model normals over k x k ray-cast pixels (1 = off)
    # The Open3D model's pull along a table (module docstring, "The pull along a table"; ENGINE_PLAN X0a). Open3D
    # model only, the GPU model ignores all three; None: off (ohs.app.pipeline, "The CPU path's defaults", says
    # where one is filled in).
    # The model's normals: "gradient" (the TSDF's gradients, as before; also None) or "vertex_map" (from the
    # refined vertex map's central differences); box-averaged over model_normal_window either way.
    model_normals: str | None = None
    # ... the box average of the vertex map's normals (k x k pixels, 1 = none); None: model_normal_window
    vertex_map_window: int | None = None
    # The GPU model's border rule (ohs.track.volume_gpu, rule (iv)) on Open3D's ray cast: a hit is kept only when
    # 8 probes on a circle of this radius (mm) around it, in its tangent plane, lie in observed voxels. None, 0: off.
    raycast_border_mm: float | None = None
    # With the velocity guard (velocity_max_accel): the first velocity after the start or a lost frame is bounded
    # from rest (velocity_max_accel times the frame time) instead of by VELOCITY_MAX_SPEED. None, False: off.
    velocity_from_rest: bool | None = None
    extract_weight_threshold: float = 3.0
    # ICP
    icp_strides: tuple[int, ...] = (16, 8, 4)  # source subsampling per level (coarse -> fine), px
    # no level samples closer than this many pixels of a 1280 px wide image (scaled with the width): at full
    # resolution the finest level takes every 8th pixel, not every 4th. Neighbouring points share the
    # camera-fixed dot error, so the denser sampling was 3-4 times slower and no more accurate (VALIDATION.md,
    # "GPU tuning"); at 640 px and below the strides stay as they are.
    icp_min_spacing: float = 8.0
    icp_iterations: tuple[int, ...] = (8, 6, 4)
    icp_max_dist_mm: tuple[float, ...] = (15.0, 8.0, 4.0)
    huber_mm: float = 1.5  # Huber threshold at the finest level
    huber_level_frac: float = 0.3  # coarser levels: max(huber_mm, frac * correspondence distance)
    normal_cos: float = 0.6  # reject associations whose normals differ by more than ~53 deg
    icp_sigma_mm: float = 1.0  # nominal point-to-plane residual sigma (weights priors vs data)
    gyro_sigma_deg: float = 0.1  # sigma of the gyro rotation prior per frame
    rot_sigma_deg: float = 5.0  # weak rotation prior without gyro
    trans_sigma_mm: float = 20.0  # weak translation prior
    constant_velocity: bool = True
    # Open3D model only (the GPU model ignores it): the most the scanner's velocity may change between two
    # frames, as an acceleration (m/s^2), when it is carried into the next prediction (module docstring, "The
    # velocity guard"). None or 0: no limit here; ohs.app.pipeline fills in VELOCITY_MAX_ACCEL (2) on the CPU
    # path since 2026-11-20 (MAINTENANCE_PLAN F14, D7), and 0 turns it off there
    velocity_max_accel: float | None = None
    # ENGINE_PLAN X4 (b): the velocity guard on the GPU model too (module docstring, "The velocity guard"), with
    # velocity_max_accel, or VELOCITY_MAX_ACCEL where that is None (the GPU path leaves it None). Off: the GPU model
    # carries every step as it is, as before.
    gpu_velocity_guard: bool = False
    # ENGINE_PLAN X4b (c), both models (module docstring, "A weakly held step"): a frame that holds its weakest
    # translation less than weak_step_strength (info["trans_strength"]) and moved along it more than weak_step_mm
    # (mm a frame) further than the step carried before does not carry that jump into the next prediction, along
    # that direction only; its own pose is what ICP found. weak_step_mode: "hold" keeps the last carried step's
    # component along it, "undo" also leaves the frame's own jump out of the next prediction there. None: off.
    weak_step_strength: float | None = None
    weak_step_mm: float = 0.0
    weak_step_mode: str = "hold"
    # ENGINE_PLAN X4c, both models (module docstring, "A frame that slides into another fit"): a frame whose step
    # along its weakest translation (info["trans_axis"]) leaves the prediction by more than slip_check_mm is
    # registered again from slip_starts ("rest": the last pose turned by the gyro, no carried step; "fine": the
    # prediction without the coarsest ICP level), and the result the model agrees with best (the truncated
    # point-to-plane cost of the frame at each result, the frame's points without a model point counted at the cap)
    # is kept when it beats the first one by more than slip_margin (mm^2). None: off.
    slip_check_mm: float | None = None
    slip_starts: tuple[str, ...] = ("rest", "fine")
    slip_margin: float = 0.05
    # ENGINE_PLAN X4c, both models (module docstring, "The newest frame held out of the model"): a tracked frame is
    # fused into the tracking model only once the next fuse_lag frames have been registered (against the model
    # without it); its pose is what ICP found, and nothing else changes. With fuse_lag_detail_only only while the
    # tracker is in detail mode (a smooth part is tracked as before). 0: off.
    fuse_lag: int = 0
    fuse_lag_detail_only: bool = True
    # sanity gates
    min_points: int = 300  # valid source points at the finest ICP level, counted at stride 4 (scaled by the stride)
    min_model_px: int = 300  # valid model pixels in the ray cast
    min_inliers: int = 300
    min_fitness: float = 0.25
    max_rmse_mm: float = 2.5
    max_jump_mm: float = 40.0
    max_jump_deg: float = 20.0
    # diagnostics: info["degenerate"] when the weakest ICP direction (rotations scaled to mm at the
    # scene distance) has less than this fraction of the strongest one's information (heuristic:
    # sphere-on-plane ~1e-5, the same scene plus a small box ~2.5e-4, the car panel 1e-4 to 6e-4).
    # The directions below it are info["weak"] (module docstring, "Weak directions").
    degenerate_eig_ratio: float = 5e-5
    # relocalisation after failures
    max_widen: float = 4.0

    def __post_init__(self) -> None:
        n = len(self.icp_strides)
        if len(self.icp_iterations) != n or len(self.icp_max_dist_mm) != n:
            raise ValueError("icp_strides, icp_iterations and icp_max_dist_mm must have equal length")
        if self.voxel_mm <= 0 or self.trunc_mult <= 0:
            raise ValueError("voxel_mm and trunc_mult must be positive")
        if self.gpu_trunc_mult is not None and not self.gpu_trunc_mult >= 1:
            raise ValueError(f"gpu_trunc_mult must be at least 1 (voxel), got {self.gpu_trunc_mult!r}")
        if (self.gpu_back_voxels is not None and not self.gpu_back_voxels > 0) or int(self.gpu_back_after) < 0:
            raise ValueError("gpu_back_voxels must be > 0 (or None) and gpu_back_after >= 0, got "
                             f"{self.gpu_back_voxels!r}, {self.gpu_back_after!r}")
        if str(self.model_backend).strip().lower() not in MODEL_BACKENDS:
            raise ValueError(f"model_backend must be one of {MODEL_BACKENDS}, got {self.model_backend!r}")
        if int(self.gpu_volume_voxels) < 64 or int(self.gpu_volume_voxels) % 16:
            raise ValueError(f"gpu_volume_voxels must be a multiple of 16, >= 64, got {self.gpu_volume_voxels!r}")
        if self.gpu_border_mm < 0 or self.gpu_weight_threshold < 0:
            raise ValueError("gpu_border_mm and gpu_weight_threshold must be >= 0")
        if self.model_normals not in MODEL_NORMALS:
            raise ValueError(f"model_normals must be one of {MODEL_NORMALS}, got {self.model_normals!r}")
        if self.vertex_map_window is not None and int(self.vertex_map_window) < 1:
            raise ValueError(f"vertex_map_window must be at least 1 (or None), got {self.vertex_map_window!r}")
        if self.raycast_border_mm is not None and not self.raycast_border_mm >= 0:
            raise ValueError(f"raycast_border_mm must be >= 0 (or None), got {self.raycast_border_mm!r}")
        if self.weak_step_mode not in WEAK_STEP_MODES:
            raise ValueError(f"weak_step_mode must be one of {WEAK_STEP_MODES}, got {self.weak_step_mode!r}")
        if (self.weak_step_strength is not None and not self.weak_step_strength >= 0) or not self.weak_step_mm >= 0:
            raise ValueError("weak_step_strength (or None) and weak_step_mm must be >= 0, got "
                             f"{self.weak_step_strength!r}, {self.weak_step_mm!r}")
        if (self.slip_check_mm is not None and not self.slip_check_mm > 0) or not self.slip_margin >= 0:
            raise ValueError("slip_check_mm must be > 0 (or None) and slip_margin >= 0, got "
                             f"{self.slip_check_mm!r}, {self.slip_margin!r}")
        if isinstance(self.slip_starts, str):
            self.slip_starts = (self.slip_starts,)
        self.slip_starts = tuple(self.slip_starts)
        if not self.slip_starts or any(s not in SLIP_STARTS for s in self.slip_starts):
            raise ValueError(f"slip_starts must name one or more of {SLIP_STARTS}, got {self.slip_starts!r}")
        if isinstance(self.fuse_lag, bool) or int(self.fuse_lag) != self.fuse_lag or int(self.fuse_lag) < 0:
            raise ValueError(f"fuse_lag must be a whole number of frames >= 0, got {self.fuse_lag!r}")

    @property
    def trunc_mm(self) -> float:
        return self.voxel_mm * self.trunc_mult

    def initial_block_count(self) -> int:
        """Blocks for ~6 cube faces worth of surface in a 0.5 m cube (one block layer)."""
        if self.block_count is not None:
            return int(self.block_count)
        block_mm = self.voxel_mm * self.block_resolution
        return int(max(1000, 6.0 * (500.0 / block_mm) ** 2))


@dataclass
class _ModelView:
    """Refined ray cast of the model at pose ``T_wc`` (maps in that camera's frame).

    Either the whole maps are given (``dense``, the Open3D ray cast), or the GPU's ``maps`` with
    the pixel ``rays``. ICP looks up a few percent of the pixels, so the GPU view makes vertices
    and normals only where :meth:`at` asks for them, with the same arithmetic per pixel as the
    whole maps (:attr:`V`, :attr:`N`). ``maps`` is the volume's staging buffer: a GPU view is
    good until the next ray cast of its volume.
    """

    T_wc: np.ndarray
    valid: np.ndarray  # (h*w,) bool
    K: np.ndarray  # intrinsics of the maps
    size: tuple[int, int]  # (w, h)
    dense: tuple[np.ndarray, np.ndarray] | None = None  # (h*w, 3) float64 vertices and unit normals
    rays: np.ndarray | None = None  # (h*w, 3) float64 pixel rays per unit depth
    maps: np.ndarray | None = None  # (h*w, 4) float32: depth along z (0 = no hit), unit normal

    @property
    def n_valid(self) -> int:
        return int(self.valid.sum())

    @property
    def V(self) -> np.ndarray:
        """(h*w, 3) float64 vertices (zero where there is no hit)."""
        if self.dense is not None:
            return self.dense[0]
        return self.rays * self.maps[:, :1].astype(np.float64)

    @property
    def N(self) -> np.ndarray:
        """(h*w, 3) float64 unit normals, towards the camera."""
        if self.dense is not None:
            return self.dense[1]
        return self.maps[:, 1:4].astype(np.float64)

    def at(self, pix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Vertices and normals at the flat pixel indices ``pix``: ``(V[pix], N[pix])``."""
        if self.dense is not None:
            return self.dense[0][pix], self.dense[1][pix]
        m = self.maps[pix]
        return self.rays[pix] * m[:, :1].astype(np.float64), m[:, 1:4].astype(np.float64)


@dataclass
class _IcpOutput:
    T_delta: np.ndarray  # pose of the current camera in the model-view camera frame
    fitness: float = 0.0
    rmse: float = float("inf")
    n_inliers: int = 0
    n_points: int = 0
    iterations: int = 0
    min_eig_ratio: float = 0.0
    weak: list = field(default_factory=list)  # [{"kind": "slide" | "turn", "axis": [x, y, z]}]
    trans_strength: float = 0.0  # _translation_strength of the final correspondences
    trans_axis: np.ndarray | None = None  # ... and that weakest translation's direction (unit, model-view camera frame)
    ok_numeric: bool = True


def _resolve_device(spec: str) -> Any:
    """``open3d.core.Device`` for "auto" (CUDA if available, else CPU), "cpu", "cuda" or "CUDA:1"."""
    import open3d.core as o3c  # noqa: PLC0415

    s = (spec or "auto").strip()
    if s.lower() == "auto":
        return o3c.Device("CUDA:0") if o3c.cuda.is_available() else o3c.Device("CPU:0")
    if s.lower() in ("cpu", "cuda"):
        s = s.upper() + ":0"
    return o3c.Device(s)


def _scale_K(K: np.ndarray, s: float) -> np.ndarray:
    """Intrinsics for an image resized by ``s`` (pixel-centre convention, as in StereoCalibration.scaled)."""
    K2 = np.asarray(K, dtype=np.float64).copy()
    K2[0, 0] *= s
    K2[1, 1] *= s
    K2[0, 1] *= s
    K2[0, 2] = (K2[0, 2] + 0.5) * s - 0.5
    K2[1, 2] = (K2[1, 2] + 0.5) * s - 0.5
    return K2


def _pixel_rays(K: np.ndarray, w: int, h: int) -> np.ndarray:
    """(h*w, 3) rays with z = 1 through the pixel centres of a ``w x h`` image."""
    vs, us = np.mgrid[0:h, 0:w]
    y = (vs.ravel() - K[1, 2]) / K[1, 1]
    x = (us.ravel() - K[0, 2] - K[0, 1] * y) / K[0, 0]
    return np.stack([x, y, np.ones_like(x)], axis=1)


def _scene_scale(Q: np.ndarray) -> np.ndarray:
    """``[L, L, L, 1, 1, 1]`` with ``L`` the median point distance: a rotation then counts as the
    millimetres it moves the scene, so all six ICP directions are comparable."""
    L = float(np.median(np.linalg.norm(Q, axis=1))) if len(Q) else 1.0
    return np.array([L, L, L, 1.0, 1.0, 1.0])


def _scaled_information(J: np.ndarray, Q: np.ndarray) -> np.ndarray:
    """``Js^T Js`` of the point-to-plane rows ``J`` with rotations scaled to mm at the scene distance."""
    Js = J / _scene_scale(Q)
    return Js.T @ Js


def _translation_strength(J: np.ndarray, Q: np.ndarray, H: np.ndarray | None = None,
                          top: float | None = None) -> float:
    """How well a point-to-plane problem holds its weakest translation while the rotation is held
    (the gyro holds it): the smallest eigenvalue of the translation block, over the largest of the
    whole problem (rotations scaled to mm at the scene distance, as in :func:`_weak_directions`).
    ``H`` and ``top`` (its largest eigenvalue) may be passed when already computed."""
    if H is None:
        H = _scaled_information(J, Q)
    if top is None:
        top = float(np.linalg.eigvalsh(H)[-1])
    return float(max(float(np.linalg.eigvalsh(H[3:, 3:])[0]), 0.0) / max(top, 1e-30))


def _weak_directions(J: np.ndarray, Q: np.ndarray, ratio: float,
                     H: np.ndarray | None = None) -> tuple[float, list[dict]]:
    """Weak motions of a point-to-plane problem: ``(weakest / strongest eigenvalue, [{"kind", "axis"}])``.

    ``J`` (n,6) has rows ``[q x n, n]`` and ``Q`` (n,3) the points (camera frame, mm). The eigen-
    directions below ``ratio`` of the strongest span the weak subspace. It holds a ``"slide"`` when a
    translation lies (almost) in it, and a ``"turn"`` when a rotation does, about any axis: a
    sphere's, or a flat panel's normal. ``axis`` is that translation's direction or that rotation's
    axis, in the camera frame. Weak eigenvectors mix freely (a panel's two slides and its turn), so
    the subspace is classified, not each eigenvector.
    """
    ev, X = np.linalg.eigh(_scaled_information(J, Q) if H is None else H)
    top = max(float(ev[-1]), 1e-30)
    min_ratio = float(max(float(ev[0]), 0.0) / top)
    Xw = X[:, ev < ratio * top]
    weak: list[dict] = []
    if Xw.shape[1]:
        for kind, block, thr in (("slide", Xw[3:], SLIDE_SV), ("turn", Xw[:3], TURN_SV)):
            U, sv, _ = np.linalg.svd(block, full_matrices=False)
            if sv[0] >= thr:
                weak.append({"kind": kind, "axis": [round(float(a), 3) for a in U[:, 0]]})
    return min_ratio, weak


def _vertex_map_normals(V: np.ndarray, valid: np.ndarray, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """``FusionParams.model_normals = "vertex_map"``: unit normals (towards the camera) of the camera-frame vertex
    map ``V`` (h*w, 3) from central differences, and where they exist: a pixel needs its four neighbours valid
    and within :data:`VERTEX_MAP_STEP_MM` of each other in depth (the image's outermost pixels have none)."""
    Vg = V.reshape(h, w, 3)
    m = valid.reshape(h, w)
    N = np.zeros_like(Vg)
    ok = np.zeros((h, w), dtype=bool)
    dx = Vg[1:-1, 2:] - Vg[1:-1, :-2]
    dy = Vg[2:, 1:-1] - Vg[:-2, 1:-1]
    n = np.cross(dx, dy)
    nn = np.linalg.norm(n, axis=-1)
    okc = m[1:-1, 2:] & m[1:-1, :-2] & m[2:, 1:-1] & m[:-2, 1:-1] & m[1:-1, 1:-1] & (nn > 1e-9)
    z = Vg[..., 2]
    okc &= ((np.abs(z[1:-1, 2:] - z[1:-1, :-2]) < VERTEX_MAP_STEP_MM)
            & (np.abs(z[2:, 1:-1] - z[:-2, 1:-1]) < VERTEX_MAP_STEP_MM))
    n = n / np.maximum(nn, 1e-12)[..., None]
    n *= -np.sign(np.einsum("ijk,ijk->ij", n, Vg[1:-1, 1:-1]))[..., None]  # face the camera
    N[1:-1, 1:-1][okc] = n[okc]
    ok[1:-1, 1:-1] = okc
    return N.reshape(-1, 3), ok.reshape(-1)


class MarkerlessTracker:
    """Frame-to-model ICP + TSDF fusion. See the module docstring for the algorithm.

    Parameters
    ----------
    K:
        3x3 rectified left intrinsics at the depth-map resolution.
    image_size:
        ``(width, height)`` of the depth maps.
    params:
        :class:`FusionParams` (defaults if ``None``); ``model_backend`` picks the TSDF (module
        docstring).
    """

    def __init__(self, K: np.ndarray, image_size: tuple[int, int], params: FusionParams | None = None) -> None:
        try:
            import open3d as o3d  # noqa: PLC0415 (optional heavy dependency)
            import open3d.core as o3c  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError("MarkerlessTracker needs open3d (pip install open3d)") from exc

        _limit_open3d_threads(o3d)
        self._o3d, self._o3c = o3d, o3c
        self.params = params if params is not None else FusionParams()
        self.device = _resolve_device(self.params.device)
        p = self.params
        self._gpu: Any = None  # GpuTsdfVolume when the model is on the GPU
        self._vbg: Any = None
        self._gpu_status = self._make_gpu_volume(str(p.model_backend).strip().lower())
        self._trunc_mm = float(self._gpu.trunc_mm if self._gpu is not None else p.trunc_mm)  # kept after close()
        self._back_mm = self._gpu.back_mm if self._gpu is not None else None
        self._set_camera(K, image_size)
        log.info("MarkerlessTracker: %dx%d, voxel %.2f mm, trunc %.2f mm, model %s (%s), ray cast %dx%d",
                 *self.image_size, p.voxel_mm, self.trunc_mm, self.model_backend, self.model_device, *self._ray_size)
        self.reset()

    def set_camera(self, K: np.ndarray, image_size: tuple[int, int]) -> None:
        """Take the next depth maps from another camera (intrinsics ``K`` at ``(width, height)``),
        keeping the model: a merge fuses scans of different calibrations into one model this way,
        each frame at a pose given (not tracked)."""
        self._set_camera(K, image_size)

    def _set_camera(self, K: np.ndarray, image_size: tuple[int, int]) -> None:
        """Everything that depends on the depth maps' camera."""
        o3c = self._o3c
        p = self.params
        self.K = np.asarray(K, dtype=np.float64).reshape(3, 3).copy()
        self.image_size = (int(image_size[0]), int(image_size[1]))
        w, h = self.image_size
        # integration intrinsics: +0.5 px turns Open3D's truncating pixel lookup into rounding
        K_int = self.K.copy()
        K_int[0, 2] += 0.5
        K_int[1, 2] += 0.5
        self._K_int_t = o3c.Tensor(K_int, o3c.float64)
        self._ray_size = (max(1, int(round(w * p.raycast_scale))), max(1, int(round(h * p.raycast_scale))))
        self._K_ray = _scale_K(self.K, self._ray_size[0] / w) if self._ray_size[0] != w else self.K.copy()
        self._K_ray_t = o3c.Tensor(self._K_ray, o3c.float64)
        self._rays_ray = _pixel_rays(self._K_ray, *self._ray_size)
        # Open3D's ray_cast hangs if the range-map factor does not divide the image size
        self._range_down = next(f for f in (8, 4, 2, 1)
                                if self._ray_size[0] % f == 0 and self._ray_size[1] % f == 0)
        # strides, at least icp_min_spacing pixels of a 1280 px image apart (every 8th pixel at full resolution)
        spacing = max(1, int(round(float(p.icp_min_spacing) * w / 1280.0)))
        self._strides = [max(int(s), spacing) for s in p.icp_strides]
        # the same area of smooth depth whatever the finest stride: min_points is counted at stride 4
        self._min_src = max(30, int(round(p.min_points * 16.0 / float(self._strides[-1]) ** 2)))
        # source back-projection rays for every ICP level
        vs, us = np.mgrid[0:h, 0:w]
        self._src_levels = []
        for s in self._strides:
            o = s // 2  # sample the centre of each s x s block
            uu, vv = us[o::s, o::s], vs[o::s, o::s]
            y = (vv - self.K[1, 2]) / self.K[1, 1]
            x = (uu - self.K[0, 2] - self.K[0, 1] * y) / self.K[0, 0]
            self._src_levels.append((o, s, np.stack([x, y, np.ones_like(x)], axis=-1)))

    def _make_gpu_volume(self, backend: str) -> str:
        """Create the GPU volume for ``backend`` "auto" / "gpu"; returns a plain-language status."""
        if backend == "open3d":
            return "Open3D model (model_backend = \"open3d\")"
        from ohs import compute  # noqa: PLC0415
        from ohs.track.volume_gpu import GpuTsdfVolume  # noqa: PLC0415

        dev = compute.get_device()
        if dev is None:
            if backend == "gpu":
                raise RuntimeError(f"model_backend = \"gpu\" but no GPU is available: {compute.status()}")
            return f"Open3D model ({compute.status()})"
        p = self.params
        trunc = p.trunc_mm if p.gpu_trunc_mult is None else p.voxel_mm * float(p.gpu_trunc_mult)
        try:
            self._gpu = GpuTsdfVolume(dev, p.voxel_mm, trunc, p.depth_min_mm, p.depth_max_mm,
                                      edge_voxels=int(p.gpu_volume_voxels), border_mm=float(p.gpu_border_mm),
                                      back_mm=None if p.gpu_back_voxels is None else p.voxel_mm * float(p.gpu_back_voxels),
                                      back_after=int(p.gpu_back_after))
        except Exception as exc:  # build or allocation failure: the CPU path still works
            if backend == "gpu":
                raise
            log.warning("GPU tracking volume unavailable on %s (%s); using Open3D", dev.name, exc)
            self._gpu = None
            return f"Open3D model (the GPU volume failed on {dev.name}: {exc})"
        return f"GPU model on {dev.name} ({self._gpu.edge}^3 voxels, {self._gpu.size_mm:.0f} mm cube)"

    @property
    def model_backend(self) -> str:
        """``"gpu"`` (dense rolling OpenCL volume) or ``"open3d"`` (sparse VoxelBlockGrid)."""
        return "gpu" if self._gpu is not None else "open3d"

    @property
    def trunc_mm(self) -> float:
        """The model's truncation distance (mm): the GPU volume's own (``FusionParams.gpu_trunc_mult``), else
        ``FusionParams.trunc_mm``."""
        return self._trunc_mm

    @property
    def back_mm(self) -> float | None:
        """How deep behind a surface (mm, at right angles) an established voxel of the GPU model still takes an
        observation (``FusionParams.gpu_back_voxels``); ``None``: the whole truncation (always, for the Open3D model)."""
        return self._back_mm

    @property
    def model_device(self) -> str:
        """Where the model lives: the GPU's name, or the Open3D device (e.g. ``"CPU:0"``)."""
        return self._gpu.dev.name if self._gpu is not None else str(self.device)

    @property
    def model_status(self) -> str:
        """Plain-language description of the model backend choice (and why)."""
        return self._gpu_status

    @property
    def gpu_volume(self) -> Any:
        """The :class:`~ohs.track.volume_gpu.GpuTsdfVolume`, or ``None`` on the Open3D backend."""
        return self._gpu

    def close(self) -> None:
        """Free the model's memory now (GPU buffers included); the tracker cannot be used afterwards."""
        if self._gpu is not None:
            self._gpu.release()
        self._gpu = None
        self._vbg = None

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        """Forget the model and the trajectory (the next frame defines a new world)."""
        o3d, o3c = self._o3d, self._o3c
        p = self.params
        if self._gpu is not None:
            self._gpu.reset()
            self._vbg = None
        else:
            self._vbg = o3d.t.geometry.VoxelBlockGrid(
                attr_names=("tsdf", "weight"),
                attr_dtypes=(o3c.float32, o3c.float32),
                attr_channels=((1), (1)),
                voxel_size=float(p.voxel_mm),
                block_resolution=int(p.block_resolution),
                block_count=p.initial_block_count(),
                device=self.device,
            )
        self.trajectory: list[tuple[float, np.ndarray, bool]] = []
        self._n_integrated = 0
        self._pending: list[tuple[np.ndarray, np.ndarray]] = []  # tracked frames not yet fused (FusionParams.fuse_lag)
        self._frag_room = 0  # entries the grid's fragment buffer is known to hold (_reserve_fragments)
        self._initialized = False
        self._T_last = np.eye(4)  # last good pose
        self._t_last: float | None = None
        self._last_step: tuple[np.ndarray, np.ndarray, float | None] | None = None  # (R, t, dt)
        self._v_w: np.ndarray | None = None  # the velocity guard's last velocity (world, mm/s); None: at rest
        self._guard_mm = 0.0  # how far (mm a frame) the guard moved the last accepted frame's velocity
        self._weak_mm = 0.0  # ... and the weak-step rule (FusionParams.weak_step_*) its carried step
        self._R_acc = np.eye(3)  # gyro rotation accumulated since the last good frame
        self._n_lost = 0
        self._frame = 0
        self.last_result: TrackResult | None = None
        #: every frame uses ``alt_depth`` (set by the pipeline for a scan of a detailed part; module
        #: docstring, "Detailed parts"); :attr:`detail_frame` is the frame that decided it
        self.detail_mode = False
        self.detail_frame: int | None = None

    @property
    def n_integrated(self) -> int:
        """Number of frames fused into the TSDF."""
        return self._n_integrated

    @property
    def n_blocks(self) -> int:
        """Allocated Open3D blocks, or observed 16^3 bricks of the GPU volume."""
        if self._gpu is not None:
            return self._gpu.n_observed_bricks()
        return int(self._vbg.hashmap().size())

    @property
    def is_lost(self) -> bool:
        return self._n_lost > 0

    @property
    def voxel_grid(self) -> Any:
        """The underlying ``open3d.t.geometry.VoxelBlockGrid`` (mm units); Open3D backend only."""
        if self._gpu is not None:
            raise RuntimeError("the model is the GPU volume (model_backend 'gpu'): there is no Open3D grid; "
                               "use extract_mesh() / extract_points() or gpu_volume")
        return self._vbg

    def scale_weights(self, factor: float) -> None:
        """Multiply the TSDF weights in place (both backends), e.g. after a subsampled rebuild."""
        if self._gpu is not None:
            self._gpu.scale_weights(float(factor))
            return
        attr = self._vbg.attribute("weight")
        if attr.device.get_type() == self._o3c.Device.DeviceType.CPU:
            arr = attr.numpy()
            arr *= np.float32(factor)
        else:
            attr *= float(factor)

    # ------------------------------------------------------------------ depth handling
    def _clean_depth(self, depth_mm: np.ndarray) -> np.ndarray:
        d = np.asarray(depth_mm, dtype=np.float32)
        w, h = self.image_size
        if d.shape != (h, w):
            raise ValueError(f"depth has shape {d.shape}, expected {(h, w)}")
        p = self.params
        # 0 is already "no depth": only the other bad pixels need the copy (none: the input itself)
        bad = (d != 0) & (~np.isfinite(d) | (d < p.depth_min_mm) | (d > p.depth_max_mm))
        if bad.any():
            d = d.copy()
            d[bad] = 0.0
        return d

    def _source_points(self, depth: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
        """Per ICP level: back-projected points (N,3) and their normals (N,3), towards the camera.

        Normals come from forward differences on the subsampled vertex grid; points without two
        valid, continuous neighbours are dropped (they would only add unreliable associations).
        """
        out = []
        done: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}  # equal (offset, stride) levels share
        fx = self.K[0, 0]
        for o, s, rays in self._src_levels:
            if (o, s) in done:
                out.append(done[o, s])
                continue
            z = depth[o::s, o::s].astype(np.float64)
            X = rays * z[..., None]
            dx = X[:-1, 1:] - X[:-1, :-1]
            dy = X[1:, :-1] - X[:-1, :-1]
            z0, zr, zd = z[:-1, :-1], z[:-1, 1:], z[1:, :-1]
            jump = 3.0 * s * z0 / fx + 2.0  # allowed depth step between neighbours (grazing planes)
            ok = (z0 > 0) & (zr > 0) & (zd > 0) & (np.abs(zr - z0) < jump) & (np.abs(zd - z0) < jump)
            n = np.cross(dx, dy)
            nn = np.linalg.norm(n, axis=-1)
            ok &= nn > 1e-9
            P = X[:-1, :-1][ok]
            N = n[ok] / nn[ok][:, None]
            N *= -np.sign(np.einsum("ij,ij->i", N, P))[:, None]  # face the camera
            done[o, s] = (P, N)
            out.append((P, N))
        return out

    # ------------------------------------------------------------------ open3d plumbing
    def _active_keys(self) -> Any:
        """(M,3) Int32 tensor of the allocated block coordinates (key_tensor() includes free slots)."""
        hm = self._vbg.hashmap()
        return hm.key_tensor()[hm.active_buf_indices().to(self._o3c.int64)]

    def _integrate_cleaned(self, depth: np.ndarray, T_wc: np.ndarray) -> None:
        if self._gpu is not None:
            # the GPU kernel rounds to the nearest pixel itself: no +0.5 px shift (that is Open3D's quirk)
            self._gpu.integrate(depth, self.K, np.asarray(T_wc, dtype=np.float64))
            self._n_integrated += 1
            return
        o3d, o3c = self._o3d, self._o3c
        p = self.params
        img = o3d.t.geometry.Image(o3c.Tensor(np.ascontiguousarray(depth))).to(self.device)
        ext = o3c.Tensor(invert_se3(np.asarray(T_wc, dtype=np.float64)), o3c.float64)
        blocks = self._vbg.compute_unique_block_coordinates(img, self._K_int_t, ext, 1.0, p.depth_max_mm, p.trunc_mult)
        self._vbg.integrate(blocks, img, self._K_int_t, ext, 1.0, p.depth_max_mm, p.trunc_mult)
        self._n_integrated += 1

    def _raycast_weight_threshold(self) -> float:
        p = self.params
        thr = (p.gpu_weight_threshold if self._gpu is not None
               else RAYCAST_WEIGHT_THRESHOLD if p.raycast_weight_threshold is None else p.raycast_weight_threshold)
        return float(min(thr, max(self._n_integrated, 1)))

    def _raycast_gpu_start(self, T_wc: np.ndarray) -> Any:
        """Queue the GPU ray cast at ``T_wc`` (refined hits and box-averaged normals come back)."""
        return self._gpu.raycast_start(T_wc, self._K_ray, self._ray_size, self._raycast_weight_threshold(),
                                       max(int(self.params.model_normal_window), 1))

    def _raycast_gpu_finish(self, pending: Any, T_wc: np.ndarray) -> _ModelView | None:
        """Model view from the GPU maps: depth along z (0 = no hit) and unit normals towards the camera."""
        if pending is None:
            return None
        maps = pending.wait()
        valid = maps[:, 0] > 0
        w, h = self._ray_size
        return _ModelView(np.asarray(T_wc, dtype=np.float64).copy(), valid, self._K_ray, (w, h),
                          rays=self._rays_ray, maps=maps)

    def _reserve_fragments(self, n_blocks: int) -> None:
        """Make Open3D's fragment buffer larger than a ray cast of ``n_blocks`` blocks can need.

        Open3D (0.19 and 0.20, ``EstimateRange`` in ``VoxelBlockGridImpl.h``) builds the range map
        of a cast from "fragments": each block in view takes one for every 16 x 16 cells of the
        range map its outline covers. They go into a buffer the grid keeps from cast to cast,
        sized to exactly what the last cast needed. A cast that needs as many or more

        * leaves the blocks that found no room out of the range map (which ones depends on the
          order its threads ran in, so pixels come and go from run to run), and
        * reads the entries of the block that crossed the end, which nothing wrote. In a fresh
          buffer that is whatever the memory held: an index from there writes outside the range
          map (an access violation, or a corrupted heap that ends the process later, anywhere),
          and a depth from there can make a ray march for ever (``MAINTENANCE_PLAN.md`` F6).

        Left alone, every cast of a growing model is such a cast. So before a cast the buffer is
        made larger than the most that cast can need: every block times the most fragments one
        outline can take. The only way to size it from Python is a cast that needs exactly that
        many, so one is made of a tiny image of copies of one block, each a single fragment.
        With one fragment a block the unwritten entries are never read, so that cast is safe
        although it overflows on purpose. The room grows by half each time, so this is rare.
        """
        w, h = self._ray_size
        cells_w, cells_h = w // self._range_down, h // self._range_down
        per_block = -(-cells_w // _FRAGMENT_CELLS) * -(-cells_h // _FRAGMENT_CELLS)
        need = n_blocks * per_block + 1  # Open3D turns a block away already when it would fill the buffer
        if need <= self._frag_room:
            return
        o3d, o3c = self._o3d, self._o3c
        want = need + need // 2
        b = float(self.params.voxel_mm) * int(self.params.block_resolution)  # a block's edge, mm
        keys = o3c.Tensor(np.zeros((want, 3), dtype=np.int32)).to(self.device)
        # block (0, 0, 0) from one block's edge in front of it, at so short a focal length that
        # its outline is 2 x 2 cells of a 64 x 64 image: one fragment for each copy of the key
        K = o3c.Tensor(np.array([[0.2, 0.0, 32.5], [0.0, 0.2, 32.5], [0.0, 0.0, 1.0]]), o3c.float64)
        T_cw = np.eye(4)
        T_cw[:3, 3] = (-0.5 * b, -0.5 * b, b)
        level = o3d.utility.get_verbosity_level()
        o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)  # it warns, as it must here
        try:
            self._vbg.ray_cast(keys, K, o3c.Tensor(T_cw, o3c.float64), 64, 64, ["depth"],
                               1.0, 0.5 * b, 4.0 * b, 1.0, float(self.params.trunc_mult), 1)
        finally:
            o3d.utility.set_verbosity_level(level)
        self._frag_room = want

    def _raycast(self, T_wc: np.ndarray) -> _ModelView | None:
        """Ray cast the model at ``T_wc`` and refine depths with the trilinear TSDF."""
        if self._gpu is not None:
            return self._raycast_gpu_finish(self._raycast_gpu_start(T_wc), T_wc)
        o3d, o3c = self._o3d, self._o3c
        p = self.params
        n_blocks = self.n_blocks
        if n_blocks == 0:
            return None
        w, h = self._ray_size
        keys = self._active_keys()
        ext = o3c.Tensor(invert_se3(T_wc), o3c.float64)
        wthr = self._raycast_weight_threshold()
        args = (1.0, p.depth_min_mm, p.depth_max_mm, wthr, p.trunc_mult, self._range_down)
        self._reserve_fragments(n_blocks)
        with o3d.utility.VerbosityContextManager(o3d.utility.VerbosityLevel.Error):
            res = self._vbg.ray_cast(keys, self._K_ray_t, ext, w, h,
                                     ["depth", "normal", "index", "mask", "interp_ratio"], *args)
        depth_all = res.depth.cpu().numpy().reshape(h * w)
        sel = np.flatnonzero(depth_all > 0)
        index = res.index.cpu().numpy().reshape(h * w, 8)[sel]
        vals, wts = self._gather_voxels(index)
        mask = res.mask.cpu().numpy().reshape(h * w, 8)[sel]
        ratio = res.interp_ratio.cpu().numpy().reshape(h * w, 8)[sel]
        depth = depth_all[sel].astype(np.float64)
        normal = res.normal.cpu().numpy().reshape(h * w, 3)[sel].astype(np.float64)
        step = self._newton_step(vals, wts, mask, ratio, self._rays_ray[sel] @ T_wc[:3, :3].T)
        nn = np.linalg.norm(normal, axis=1)
        ok = np.isfinite(step) & (np.abs(step) < 1.5 * p.voxel_mm) & (nn > 0.5)
        sel, depth, step, normal, nn = sel[ok], depth[ok], step[ok], normal[ok], nn[ok]
        valid = np.zeros(h * w, dtype=bool)
        valid[sel] = True
        V = np.zeros((h * w, 3))
        V[sel] = self._rays_ray[sel] * (depth + step)[:, None]
        N = np.zeros((h * w, 3))
        N[sel] = -normal / nn[:, None]
        k = p.model_normal_window
        if p.model_normals == "vertex_map":
            N, valid = _vertex_map_normals(V, valid, w, h)
            k = k if p.vertex_map_window is None else int(p.vertex_map_window)
        if k > 1:
            N, valid = self._smooth_normals(N, valid, w, h, k)
        if p.raycast_border_mm:
            valid = valid & self._inside_observed(V, N, valid, T_wc, float(p.raycast_border_mm))
        return _ModelView(T_wc.copy(), valid, self._K_ray, (w, h), dense=(V, N))

    def _observed(self, X: np.ndarray) -> np.ndarray:
        """(N,) bool: the voxel nearest to each world point (mm) ``X`` (N,3) has been fused (weight > 0)."""
        o3c = self._o3c
        p = self.params
        r = int(p.block_resolution)
        g = np.floor(X / float(p.voxel_mm) + 0.5).astype(np.int64)  # voxel i sits at i * voxel_mm
        blk = np.floor_divide(g, r)
        loc = g - blk * r
        off = 1 << 20
        code = ((blk[:, 0] + off) << 42) | ((blk[:, 1] + off) << 21) | (blk[:, 2] + off)
        uc, inv = np.unique(code, return_inverse=True)
        m21 = (1 << 21) - 1
        ub = np.stack([(uc >> 42) - off, ((uc >> 21) & m21) - off, (uc & m21) - off], axis=1)
        buf, found = self._vbg.hashmap().find(o3c.Tensor(ub.astype(np.int32)).to(self.device))
        buf = buf.cpu().numpy().astype(np.int64).reshape(-1)[inv.reshape(-1)]
        have = found.cpu().numpy().astype(bool).reshape(-1)[inv.reshape(-1)]
        lin = buf * r ** 3 + (loc[:, 2] * r + loc[:, 1]) * r + loc[:, 0]  # Open3D's order within a block
        out = np.zeros(len(X), dtype=bool)
        if have.any():
            _, wts = self._gather_voxels(lin[have])
            out[have] = wts > 0
        return out

    def _inside_observed(self, V: np.ndarray, N: np.ndarray, valid: np.ndarray, T_wc: np.ndarray,
                         radius_mm: float) -> np.ndarray:
        """``FusionParams.raycast_border_mm``: which hits have observed voxels on a circle of ``radius_mm`` around
        them in their tangent plane (:data:`BORDER_PROBES` probes; the GPU model's rule (iv), with nearest-voxel
        lookups). ``V``/``N`` are the camera-frame maps at ``T_wc``."""
        sel = np.flatnonzero(valid)
        out = np.zeros(len(valid), dtype=bool)
        if len(sel) == 0:
            return out
        R, t = T_wc[:3, :3], T_wc[:3, 3]
        X = V[sel] @ R.T + t
        n = N[sel] @ R.T
        a = np.where(np.abs(n[:, :1]) < 0.9, np.array([[1.0, 0.0, 0.0]]), np.array([[0.0, 1.0, 0.0]]))
        t1 = np.cross(n, a)
        t1 /= np.linalg.norm(t1, axis=1, keepdims=True)
        t2 = np.cross(n, t1)
        ang = 2.0 * np.pi * np.arange(BORDER_PROBES) / BORDER_PROBES
        Q = X[None] + radius_mm * (np.cos(ang)[:, None, None] * t1[None] + np.sin(ang)[:, None, None] * t2[None])
        out[sel] = self._observed(Q.reshape(-1, 3)).reshape(BORDER_PROBES, -1).all(axis=0)
        return out

    def _newton_step(self, vals: np.ndarray, wts: np.ndarray, mask: np.ndarray, ratio: np.ndarray,
                     rays_w: np.ndarray) -> np.ndarray:
        """Depth correction (mm) moving each ray-cast hit onto the zero crossing of the trilinear TSDF.

        ``vals``/``wts``/``mask``/``ratio`` are the (n,8) corner TSDF values, weights, existence
        flags and trilinear weights of Open3D's ray cast (corner ``j`` = offset
        ``(j & 1, j >> 1 & 1, j >> 2 & 1)`` voxels, verified empirically); ``rays_w`` (n,3) are
        the world-frame ray directions per unit depth. Where all eight corners are observed this
        is an exact Newton step on the trilinear field (its gradient along the ray is computed
        analytically); otherwise it falls back to the projective approximation
        ``d(tsdf * trunc)/dt = -1``. NaN marks rays that cannot be refined.
        """
        p = self.params
        good = mask & (wts > 0)
        r = np.where(good, ratio, 0.0)
        rsum = r.sum(axis=1)
        tsdf = (vals * r).sum(axis=1) / np.maximum(rsum, 1e-6)
        step = np.where(rsum > 0.5, tsdf * p.trunc_mm, np.nan)  # projective fallback
        full = good.all(axis=1)
        if full.any():
            fx, fy, fz = (ratio[full] @ _CORNERS).T  # fractional position inside the voxel cell
            gx, gy, gz = 1.0 - fx, 1.0 - fy, 1.0 - fz
            v0, v1, v2, v3, v4, v5, v6, v7 = vals[full].T
            # analytic gradient of the trilinear interpolant (per voxel), then per mm
            dx = gy * gz * (v1 - v0) + fy * gz * (v3 - v2) + gy * fz * (v5 - v4) + fy * fz * (v7 - v6)
            dy = gx * gz * (v2 - v0) + fx * gz * (v3 - v1) + gx * fz * (v6 - v4) + fx * fz * (v7 - v5)
            dz = gx * gy * (v4 - v0) + fx * gy * (v5 - v1) + gx * fy * (v6 - v2) + fx * fy * (v7 - v3)
            rw = rays_w[full]
            dfdt = (dx * rw[:, 0] + dy * rw[:, 1] + dz * rw[:, 2]) / p.voxel_mm
            steep = dfdt < -0.2 / p.trunc_mm  # the ray must clearly enter the surface
            exact = np.full(len(dfdt), np.nan)
            exact[steep] = -tsdf[full][steep] / dfdt[steep]
            step[full] = exact
        return step

    def _gather_voxels(self, index: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """TSDF values and weights at linear voxel indices (any shape).

        On the CPU the voxel buffers are read through zero-copy numpy views; on CUDA the gather runs
        on the device and only the gathered values are copied back.
        """
        o3c = self._o3c
        tsdf_attr, wgt_attr = self._vbg.attribute("tsdf"), self._vbg.attribute("weight")
        n_vox = int(np.prod(tsdf_attr.shape))
        idx = np.clip(index, 0, n_vox - 1)
        if self.device.get_type() == o3c.Device.DeviceType.CPU:
            return tsdf_attr.numpy().reshape(-1)[idx], wgt_attr.numpy().reshape(-1)[idx]
        flat = o3c.Tensor(idx.reshape(-1)).to(self.device)
        vals = tsdf_attr.reshape((-1,))[flat].cpu().numpy().reshape(idx.shape)
        wts = wgt_attr.reshape((-1,))[flat].cpu().numpy().reshape(idx.shape)
        return vals, wts

    @staticmethod
    def _smooth_normals(N: np.ndarray, valid: np.ndarray, w: int, h: int, k: int) -> tuple[np.ndarray, np.ndarray]:
        """Validity-weighted k x k box average of the normal map, renormalised.

        TSDF-gradient normals of a young or 1 mm model are noisy (~18 deg median after one frame);
        noisy normals add spurious "friction" to point-to-plane ICP along sliding directions and
        make it converge very slowly, so they are averaged over a small window.
        """
        import cv2  # noqa: PLC0415

        m = valid.reshape(h, w).astype(np.float32)
        acc = cv2.boxFilter((N.reshape(h, w, 3) * m[..., None]).astype(np.float32), -1, (k, k),
                            normalize=False, borderType=cv2.BORDER_CONSTANT)
        acc = acc.reshape(-1, 3).astype(np.float64)
        nrm = np.linalg.norm(acc, axis=1)
        ok = valid & (nrm > 1e-6)
        return np.divide(acc, nrm[:, None], out=np.zeros_like(acc), where=ok[:, None]), ok

    # ------------------------------------------------------------------ ICP
    def _icp(self, src: list[tuple[np.ndarray, np.ndarray]], view: _ModelView, widen: float,
             rot_sigma_rad: float, extra_coarse: bool, skip_coarse: int = 0) -> _IcpOutput:
        """Projective point-to-plane Gauss-Newton; returns the pose of the frame in the view frame. ``skip_coarse``:
        that many of the coarsest levels are left out (the slip check's "fine" start)."""
        p = self.params
        K = view.K
        w, h = view.size
        tvalid = view.valid
        sig2 = p.icp_sigma_mm ** 2
        lam_r = sig2 / rot_sigma_rad ** 2
        lam_t = sig2 / p.trans_sigma_mm ** 2
        R, t = np.eye(3), np.zeros(3)
        levels = [(P, N, n_it, dmax) for (P, N), n_it, dmax in zip(src, p.icp_iterations, p.icp_max_dist_mm)]
        if extra_coarse:
            P0, N0 = src[0]
            levels = [(P0[::4], N0[::4], 2 * p.icp_iterations[0], p.icp_max_dist_mm[0] * 1.5)] + levels
        if skip_coarse:
            levels = levels[min(int(skip_coarse), len(levels) - 1):]
        total_it = 0
        out = _IcpOutput(np.eye(4))

        def associate(P: np.ndarray, N: np.ndarray, R: np.ndarray, t: np.ndarray,
                      dmax: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            """Inlier correspondences: (Q, model V, model N) after distance and normal gates."""
            Q = P @ R.T + t
            z = Q[:, 2]
            zs = np.where(z > 1e-6, z, 1.0)
            u = np.rint(K[0, 0] * Q[:, 0] / zs + K[0, 1] * Q[:, 1] / zs + K[0, 2]).astype(np.int64)
            v = np.rint(K[1, 1] * Q[:, 1] / zs + K[1, 2]).astype(np.int64)
            inside = (z > 1e-6) & (u >= 0) & (u < w) & (v >= 0) & (v < h)
            sel = np.flatnonzero(inside)
            pix = v[sel] * w + u[sel]
            ok = tvalid[pix]
            sel, pix = sel[ok], pix[ok]
            Q = Q[sel]
            Vt, Nt = view.at(pix)
            diff = Q - Vt
            ok = np.einsum("ij,ij->i", diff, diff) < dmax * dmax
            ok &= np.einsum("ij,ij->i", N[sel] @ R.T, Nt) > p.normal_cos
            return Q[ok], Vt[ok], Nt[ok]

        for li, (P, Ns, n_it, dmax) in enumerate(levels):
            if li < len(levels) - 1:  # the finest level is never widened: it decides the gates
                dmax = dmax * widen
            # a small Huber threshold at coarse levels caps every step at ~huber_mm (IRLS), so it
            # grows with the correspondence distance; the fine level uses huber_mm
            huber = max(p.huber_mm, p.huber_level_frac * dmax)
            if len(P) < 6:
                continue
            for _ in range(n_it):
                Q, Vt, Nt = associate(P, Ns, R, t, dmax)
                if len(Q) < 6:
                    break
                r = np.einsum("ij,ij->i", Q - Vt, Nt)
                ar = np.abs(r)
                wgt = np.where(ar <= huber, 1.0, huber / np.maximum(ar, 1e-12))
                J = np.empty((len(r), 6))
                J[:, :3] = np.cross(Q, Nt)
                J[:, 3:] = Nt
                Jw = J * wgt[:, None]
                H = Jw.T @ J / sig2
                g = Jw.T @ r / sig2
                # priors relative to the prediction (left-perturbation Jacobians)
                rot_res = so3_log(R)
                Jp = np.zeros((6, 6))
                Jp[:3, :3] = np.eye(3)
                Jp[3:, :3] = -skew(t)
                Jp[3:, 3:] = np.eye(3)
                Lam = np.diag([lam_r] * 3 + [lam_t] * 3)
                H = H + Jp.T @ Lam @ Jp
                g = g + Jp.T @ Lam @ np.concatenate([rot_res, t])
                d = 1.0 / np.sqrt(np.maximum(np.diag(H), 1e-30))
                Hs = H * d[:, None] * d[None, :]
                try:
                    xi = -d * np.linalg.solve(Hs, d * g)
                except np.linalg.LinAlgError:
                    xi = -d * np.linalg.lstsq(Hs, d * g, rcond=None)[0]
                if not np.all(np.isfinite(xi)):
                    out.ok_numeric = False
                    return out
                dR = so3_exp(xi[:3])
                R = dR @ R
                t = dR @ t + xi[3:]
                total_it += 1
                if log.isEnabledFor(logging.DEBUG):
                    log.debug("icp it %d dmax %.1f: n %d/%d |dw| %.2e |dv| %.2e rms %.3f",
                              total_it, dmax, len(r), len(P), np.linalg.norm(xi[:3]), np.linalg.norm(xi[3:]),
                              float(np.sqrt(np.mean(r * r))))
                if np.linalg.norm(xi[:3]) < 1e-6 and np.linalg.norm(xi[3:]) < 1e-4:
                    break
        # final statistics at the finest level
        P, Ns = src[-1]
        Q, Vt, Nt = associate(P, Ns, R, t, p.icp_max_dist_mm[-1])
        r = np.einsum("ij,ij->i", Q - Vt, Nt)
        n_in = int(len(r))
        out.T_delta = make_se3(R, t)
        out.n_points = int(len(P))
        out.n_inliers = n_in
        out.fitness = n_in / max(len(P), 1)
        out.rmse = float(np.sqrt(np.mean(r ** 2))) if n_in else float("inf")
        out.iterations = total_it
        if n_in >= 6:
            J = np.concatenate([np.cross(Q, Nt), Nt], axis=1)
            H = _scaled_information(J, Q)
            out.min_eig_ratio, out.weak = _weak_directions(J, Q, p.degenerate_eig_ratio, H)
            out.trans_strength = _translation_strength(J, Q, H)
            out.trans_axis = np.linalg.eigh(H[3:, 3:])[1][:, 0]
        return out

    # ------------------------------------------------------------------ public API
    def track(self, depth_mm: np.ndarray, intensity: np.ndarray | None = None, t: float | None = None,
              R_prior: np.ndarray | None = None,
              alt_depth: np.ndarray | Callable[[], np.ndarray] | None = None) -> TrackResult:
        """Track one depth frame against the model and fuse it on success.

        ``depth_mm`` (H,W) float mm, 0/NaN invalid; ``t`` camera timestamp (s, optional, used for
        the constant-velocity model); ``R_prior`` gyro rotation from the previous call's frame to
        this one (``v_prev = R_prior @ v_cur``), or ``None``.

        ``alt_depth``: another depth map of the same frame (or a function that makes it). With
        :attr:`detail_mode` every frame uses it (and falls back to ``depth_mm`` for the pose when it
        fails); otherwise a frame tries it when ``depth_mm`` has too little depth or fails a gate.
        Whichever registered the frame is what gets fused. ``info["input"]`` is ``"alt"`` for a
        frame that used it, and ``info["trans_strength"]`` says how well the registered input held
        its weakest translation (module docstring, "Detailed parts").
        """
        t0 = time.perf_counter()
        p = self.params
        self._frame += 1
        alt_cache: list[np.ndarray] = []

        def alt() -> np.ndarray:
            if not alt_cache:
                a = alt_depth() if callable(alt_depth) else alt_depth
                alt_cache.append(self._clean_depth(a))
            return alt_cache[0]

        used_alt = alt_depth is not None and self.detail_mode
        depth = alt() if used_alt else self._clean_depth(depth_mm)
        # counted on the comparison: numpy counts a bool array several times faster than floats
        n_valid_px = int(np.count_nonzero(depth != 0))
        timings: dict[str, float] = {}
        if alt_depth is not None and not used_alt and n_valid_px < p.min_points:
            depth, used_alt = alt(), True
            n_valid_px = int(np.count_nonzero(depth != 0))

        if not self._initialized:
            if n_valid_px < p.min_points:
                return self._fail("no_depth", t, {"n_valid_px": n_valid_px}, t0, timings)
            ti = time.perf_counter()
            self._integrate_cleaned(depth, np.eye(4))
            timings["integrate"] = 1e3 * (time.perf_counter() - ti)
            self._initialized = True
            self._accept(np.eye(4), t, None)
            info = {"first_frame": True, "n_valid_px": n_valid_px}
            return self._finish(TrackResult(np.eye(4), True, info), t, t0, timings)

        if R_prior is not None:
            R_prior = np.asarray(R_prior, dtype=np.float64).reshape(3, 3)
            self._R_acc = self._R_acc @ R_prior
        if n_valid_px < p.min_points:
            return self._fail("no_depth", t, {"n_valid_px": n_valid_px}, t0, timings)

        # ---- prediction (before the source points: the GPU ray cast then runs while they are computed)
        lost = self._n_lost
        widen = float(min(1.0 + lost, p.max_widen))
        have_prior = R_prior is not None
        if lost == 0:
            name = "motion_model"
            R_rel, t_rel = np.eye(3), np.zeros(3)
            if p.constant_velocity and self._last_step is not None:
                R_s, t_s, dt_s = self._last_step
                ratio = 1.0
                if dt_s and t is not None and self._t_last is not None and dt_s > 0:
                    ratio = float(np.clip((t - self._t_last) / dt_s, 0.0, 3.0))
                t_rel = ratio * t_s
                R_rel = so3_exp(ratio * so3_log(R_s))
            if have_prior:
                R_rel = self._R_acc
            T_pred = self._T_last @ make_se3(R_rel, t_rel)
        elif have_prior:
            # relocalise around the last good pose rotated by the gyro since then. A stale
            # rotation is never tried: on symmetric geometry it would pass every gate, wrongly.
            name = "gyro_reloc"
            T_pred = self._T_last @ make_se3(self._R_acc)
        else:
            name = "last_good"
            T_pred = self._T_last.copy()
        # the gyro prior's uncertainty grows with the number of frames integrated since the last good pose
        rot_sigma = np.radians(p.gyro_sigma_deg * np.sqrt(lost + 1) if have_prior else p.rot_sigma_deg)

        pending = None
        tr = time.perf_counter()
        if self._gpu is not None:
            pending = self._raycast_gpu_start(T_pred)
        t_queue = time.perf_counter() - tr
        src = self._source_points(depth)
        if len(src[-1][0]) < self._min_src and alt_depth is not None and not used_alt:
            depth, used_alt = alt(), True
            src = self._source_points(depth)
        if len(src[-1][0]) < self._min_src:  # depth present but no locally smooth surface in it
            if pending is not None:
                pending.wait()
            return self._fail("bad_depth", t, {"n_valid_px": n_valid_px}, t0, timings)

        tr = time.perf_counter()
        view = self._raycast_gpu_finish(pending, T_pred) if self._gpu is not None else self._raycast(T_pred)
        timings["raycast"] = 1e3 * (time.perf_counter() - tr + t_queue)  # GPU: queueing + waiting
        info: dict[str, Any] = {"candidate": name, "lost_frames": lost, "gyro_prior": have_prior,
                                "n_valid_px": n_valid_px, "T_pred": T_pred}
        if view is None or view.n_valid < p.min_model_px:
            info["n_model_px"] = 0 if view is None else view.n_valid
            return self._fail("model_not_visible", t, info, t0, timings)
        tc = time.perf_counter()
        res = self._icp(src, view, widen, rot_sigma, extra_coarse=lost > 0)
        T_est = view.T_wc @ res.T_delta
        reason = self._gate(res, T_pred, T_est, widen)
        src_res = src  # the points that registered the frame (the slip check registers them again)
        if alt_depth is not None and not used_alt and reason is not None:
            # the frame fails with its input: try the other depth map before losing it
            a = alt()
            src2 = self._source_points(a)
            if len(src2[-1][0]) >= self._min_src:
                res2 = self._icp(src2, view, widen, rot_sigma, extra_coarse=lost > 0)
                T2 = view.T_wc @ res2.T_delta
                if self._gate(res2, T_pred, T2, widen) is None:
                    res, T_est, reason, depth, used_alt = res2, T2, None, a, True
                    src_res = src2
            info["alt_tried"] = True
        elif alt_depth is not None and used_alt and self.detail_mode and reason is not None:
            # detail mode: if the frame fails with the part (a model that hasn't seen this part of
            # it yet), register it with the main input and fuse the part all the same
            m = self._clean_depth(depth_mm)
            src_m = self._source_points(m)
            if len(src_m[-1][0]) >= self._min_src:
                res_m = self._icp(src_m, view, widen, rot_sigma, extra_coarse=lost > 0)
                T_m = view.T_wc @ res_m.T_delta
                if self._gate(res_m, T_pred, T_m, widen) is None:
                    res, T_est, reason = res_m, T_m, None
                    src_res = src_m
            info["main_tried"] = True
        if reason is None and lost == 0 and p.slip_check_mm is not None and res.trans_axis is not None:
            # a step that slid along the weakly held direction: registered again from other starts (module docstring,
            # "A frame that slides into another fit"; FusionParams.slip_check_mm)
            res, T_est, view, slip = self._slip_check(src_res, view, res, T_est, T_pred, rot_sigma)
            if slip is not None:
                info["slip_check"] = slip
        timings["icp"] = 1e3 * (time.perf_counter() - tc)
        info["input"] = "alt" if used_alt else "main"
        D = invert_se3(T_pred) @ T_est
        info.update({
            "fitness": res.fitness, "rmse_mm": res.rmse, "n_inliers": res.n_inliers, "n_points": res.n_points,
            "n_model_px": view.n_valid, "iterations": res.iterations,
            "jump_mm": float(np.linalg.norm(D[:3, 3])), "jump_deg": rotation_angle_deg(D[:3, :3]),
            "min_eig_ratio": res.min_eig_ratio, "degenerate": res.min_eig_ratio < p.degenerate_eig_ratio,
            "weak": res.weak, "trans_strength": res.trans_strength,
        })
        axis_w = None
        if res.trans_axis is not None:
            info["trans_axis"] = [round(float(a), 4) for a in res.trans_axis]
            axis_w = view.T_wc[:3, :3] @ res.trans_axis
        if reason is not None:
            return self._fail(reason, t, info, t0, timings)
        ti = time.perf_counter()
        lag = self._fuse_lag()
        if lag or self._pending:
            # the newest frames held out of the model (FusionParams.fuse_lag): fused once the next ones registered
            self._pending.append((np.array(depth, dtype=np.float32, copy=True), T_est.copy()))
            while len(self._pending) > lag:
                self._integrate_cleaned(*self._pending.pop(0))
            info["fuse_lag"] = len(self._pending)
        else:
            self._integrate_cleaned(depth, T_est)
        timings["integrate"] = 1e3 * (time.perf_counter() - ti)
        if lost:
            log.info("markerless: relocalised after %d lost frames (%s, fitness %.2f)", lost, name, res.fitness)
        self._accept(T_est, t, consecutive=(lost == 0),
                     weak=None if axis_w is None else (float(res.trans_strength), axis_w))
        if self._guard_mm > 0:  # the velocity guard changed what this frame carries into the next prediction
            info["velocity_guard_mm"] = self._guard_mm
        if self._weak_mm > 0:  # a weakly held jump was not carried into the next prediction (FusionParams.weak_step_*)
            info["weak_step_mm"] = self._weak_mm
        return self._finish(TrackResult(T_est.copy(), True, info), t, t0, timings)

    def register(self, depth_mm: np.ndarray, T_start: np.ndarray, gyro: bool = True) -> tuple[np.ndarray | None, dict]:
        """Register one depth map against the model from ``T_start`` (its rotation held as a gyro rotation when
        ``gyro``), without fusing it or changing the tracker's state: ``(T_wc or None when a gate fails, info)``.
        The scan's start uses it (``ohs.app.pipeline``, "The scan's start")."""
        p = self.params
        depth = self._clean_depth(depth_mm)
        T_start = np.asarray(T_start, dtype=np.float64)
        src = self._source_points(depth)
        if len(src[-1][0]) < self._min_src:
            return None, {"reason": "bad_depth"}
        view = self._raycast(T_start)
        if view is None or view.n_valid < p.min_model_px:
            return None, {"reason": "model_not_visible"}
        res = self._icp(src, view, 1.0, np.radians(p.gyro_sigma_deg if gyro else p.rot_sigma_deg), extra_coarse=False)
        T_est = view.T_wc @ res.T_delta
        reason = self._gate(res, T_start, T_est, 1.0)
        info = {"fitness": res.fitness, "rmse_mm": res.rmse, "n_inliers": res.n_inliers,
                "trans_strength": res.trans_strength, "reason": reason,
                "jump_mm": float(np.linalg.norm((invert_se3(T_start) @ T_est)[:3, 3]))}
        return (None if reason is not None else T_est), info

    def rebuild(self, frames: list[tuple[np.ndarray, np.ndarray]]) -> None:
        """Forget the model (not the trajectory) and fuse ``frames`` ``[(depth, T_wc), ...]`` into it again."""
        self._pending = []
        if self._gpu is not None:
            self._gpu.reset()
        else:
            o3d, o3c = self._o3d, self._o3c
            p = self.params
            self._vbg = o3d.t.geometry.VoxelBlockGrid(
                attr_names=("tsdf", "weight"), attr_dtypes=(o3c.float32, o3c.float32), attr_channels=((1), (1)),
                voxel_size=float(p.voxel_mm), block_resolution=int(p.block_resolution),
                block_count=p.initial_block_count(), device=self.device)
            self._frag_room = 0
        self._n_integrated = 0
        for depth, T in frames:
            self._integrate_cleaned(self._clean_depth(depth), np.asarray(T, dtype=np.float64))

    def restart(self, frames: list[tuple[np.ndarray, np.ndarray, float | None]]) -> None:
        """The model and the state as if ``frames`` ``[(depth, T_wc, t), ...]`` (consecutive, the first the
        world's frame) had just been tracked at those poses: the model rebuilt from them, the last pose and the
        step to it carried into the next prediction, the trajectory those poses."""
        self.rebuild([(d, T) for d, T, _ in frames])
        self._initialized = bool(frames)
        self.trajectory = [(float("nan") if t is None else float(t), np.asarray(T, dtype=np.float64).copy(), True)
                           for _, T, t in frames]
        self._frame = len(frames)
        self._v_w = None
        self._last_step = None
        self._T_last = np.eye(4)
        self._t_last = None
        for k, (_, T, t) in enumerate(frames):
            self._accept(np.asarray(T, dtype=np.float64), t, consecutive=None if k == 0 else True)

    def _fuse_lag(self) -> int:
        """How many of the newest tracked frames stay out of the model (``FusionParams.fuse_lag``; module docstring,
        "The newest frame held out of the model"): 0 when off, or with ``fuse_lag_detail_only`` outside detail mode."""
        p = self.params
        if not p.fuse_lag or (p.fuse_lag_detail_only and not self.detail_mode):
            return 0
        return int(p.fuse_lag)

    def flush_pending(self) -> None:
        """Fuse the tracked frames still held out of the model (``FusionParams.fuse_lag``) now."""
        while self._pending:
            self._integrate_cleaned(*self._pending.pop(0))

    def _gate(self, res: _IcpOutput, T_pred: np.ndarray, T_est: np.ndarray, widen: float) -> str | None:
        """Failure reason of an ICP result, or ``None`` if it is acceptable."""
        p = self.params
        if not res.ok_numeric or not np.all(np.isfinite(T_est)):
            return "diverged"
        if res.n_inliers < p.min_inliers:
            return "few_inliers"
        if res.fitness < p.min_fitness:
            return "low_fitness"
        if res.rmse > p.max_rmse_mm:
            return "high_rmse"
        D = invert_se3(T_pred) @ T_est
        if np.linalg.norm(D[:3, 3]) > p.max_jump_mm * widen or rotation_angle_deg(D[:3, :3]) > p.max_jump_deg * widen:
            return "jump"
        return None

    def _slip_check(self, src: list[tuple[np.ndarray, np.ndarray]], view: _ModelView, res: _IcpOutput,
                    T_est: np.ndarray, T_pred: np.ndarray,
                    rot_sigma: float) -> tuple[_IcpOutput, np.ndarray, _ModelView, dict | None]:
        """``FusionParams.slip_check_mm`` (module docstring, "A frame that slides into another fit"): when the frame's
        step along its weakest translation leaves the prediction by more than the limit, the frame is registered again
        from ``slip_starts`` and the result the model agrees with best (:meth:`_agreement`) is kept, if it beats the
        first by more than ``slip_margin``. Returns ``(res, T_est, its view, what was done or None)``."""
        p = self.params
        axis_w = view.T_wc[:3, :3] @ res.trans_axis
        along = float((T_est[:3, 3] - T_pred[:3, 3]) @ axis_w)
        if not abs(along) > p.slip_check_mm:
            return res, T_est, view, None
        cands = [("first", res, T_est, view)]
        for name in p.slip_starts:
            if name == "rest":  # where the frame is without the carried step, turned by the gyro (as the prediction)
                T_s = self._T_last.copy()
                T_s[:3, :3] = T_pred[:3, :3]
                skip = 0
            else:  # "fine": the prediction, without the coarsest level (its wide gates are what slides)
                T_s, skip = T_pred, 1
            v = self._raycast(T_s)
            if v is None or v.n_valid < p.min_model_px:
                continue
            r = self._icp(src, v, 1.0, rot_sigma, extra_coarse=False, skip_coarse=skip)
            T = v.T_wc @ r.T_delta
            if r.trans_axis is not None and self._gate(r, T_pred, T, 1.0) is None:
                cands.append((name, r, T, v))
        costs = [self._agreement(src[-1], T) for _, _, T, _ in cands]
        best = int(np.argmin(costs))
        keep = best if costs[best] < costs[0] - p.slip_margin else 0
        name, r, T, v = cands[keep]
        out = {"along_mm": round(along, 3), "kept": name,
               "costs": {c[0]: round(float(x), 4) for c, x in zip(cands, costs)},
               "moved_mm": round(float(np.linalg.norm(T[:3, 3] - T_est[:3, 3])), 3)}
        if keep:
            log.debug("markerless frame %d: slid %.1f mm along its weak direction; kept the %s start's result "
                      "(%.2f mm away)", self._frame, along, name, out["moved_mm"])
        return r, T, v, out

    def _agreement(self, src_fine: tuple[np.ndarray, np.ndarray], T_wc: np.ndarray) -> float:
        """How well the model agrees with the frame's finest-level points at ``T_wc`` (lower is better): the mean
        point-to-plane residual squared, truncated at the finest correspondence distance, over all the points, a
        point without a model point within that distance (or with a normal too different) counted at the cap. The
        model is ray cast at ``T_wc`` itself, so results from different starts are judged alike."""
        p = self.params
        P, Ns = src_fine
        cap2 = float(p.icp_max_dist_mm[-1]) ** 2
        n = len(P)
        v = self._raycast(T_wc)
        if v is None or n == 0:
            return float("inf")
        K = v.K
        w, h = v.size
        z = P[:, 2]
        zs = np.where(z > 1e-6, z, 1.0)
        u = np.rint(K[0, 0] * P[:, 0] / zs + K[0, 1] * P[:, 1] / zs + K[0, 2]).astype(np.int64)
        vv = np.rint(K[1, 1] * P[:, 1] / zs + K[1, 2]).astype(np.int64)
        sel = np.flatnonzero((z > 1e-6) & (u >= 0) & (u < w) & (vv >= 0) & (vv < h))
        pix = vv[sel] * w + u[sel]
        ok = v.valid[pix]
        sel, pix = sel[ok], pix[ok]
        Vt, Nt = v.at(pix)
        diff = P[sel] - Vt
        good = (np.einsum("ij,ij->i", diff, diff) < cap2) & (np.einsum("ij,ij->i", Ns[sel], Nt) > p.normal_cos)
        r = np.einsum("ij,ij->i", diff[good], Nt[good])
        return float(np.minimum(r * r, cap2).sum() + (n - len(r)) * cap2) / n

    def _accept(self, T_wc: np.ndarray, t: float | None, consecutive: bool | None,
                weak: tuple[float, np.ndarray] | None = None) -> None:
        self._guard_mm = 0.0
        self._weak_mm = 0.0
        if consecutive:
            D = invert_se3(self._T_last) @ T_wc
            dt = None if (t is None or self._t_last is None) else float(t - self._t_last)
            step = D[:3, 3].copy()
            accel = self._guard_accel()
            if accel and dt:
                step = self._guard_velocity(step, dt, accel)
            if weak is not None:
                step = self._weak_step(step, dt, *weak)
            self._last_step = (D[:3, :3].copy(), step, dt)
        else:
            self._last_step = None
            self._v_w = None
        self._T_last = np.asarray(T_wc, dtype=np.float64).copy()
        self._t_last = t
        self._R_acc = np.eye(3)
        self._n_lost = 0

    def _guard_accel(self) -> float | None:
        """The velocity guard's acceleration (m/s^2), or None when it is off: ``velocity_max_accel`` on the Open3D
        model; on the GPU model only with ``gpu_velocity_guard`` (then :data:`VELOCITY_MAX_ACCEL` where it is None)."""
        p = self.params
        if self._gpu is None:
            return p.velocity_max_accel or None
        if not p.gpu_velocity_guard:
            return None
        return float(p.velocity_max_accel) if p.velocity_max_accel else VELOCITY_MAX_ACCEL

    def _guard_velocity(self, step: np.ndarray, dt: float, accel: float) -> np.ndarray:
        """The velocity guard (module docstring): the frame's translation ``step`` (mm, in the previous
        frame's camera frame, over ``dt`` s) as it is carried into the next prediction, its velocity
        changed from the last one by at most ``accel * dt`` (``accel`` in m/s^2). Sets :attr:`_guard_mm`."""
        R_last = self._T_last[:3, :3]
        v = R_last @ step / dt  # mm/s, world
        accel_lim = float(accel) * 1000.0 * dt  # mm/s
        if self._v_w is None:  # the first velocity of a run of frames: the scanner's speed is not known, only bounded
            # (from rest with FusionParams.velocity_from_rest)
            ref, lim = np.zeros(3), accel_lim if self.params.velocity_from_rest else VELOCITY_MAX_SPEED
        else:
            ref, lim = self._v_w, accel_lim
        dv = v - ref
        n = float(np.linalg.norm(dv))
        if n > lim:
            v = ref + dv * (lim / n)
            self._guard_mm = (n - lim) * dt
            step = R_last.T @ v * dt
        self._v_w = v
        return step

    def _weak_step(self, step: np.ndarray, dt: float | None, strength: float, axis_w: np.ndarray) -> np.ndarray:
        """``FusionParams.weak_step_*`` (module docstring, "A weakly held step"): the frame's translation ``step``
        (mm, in the previous frame's camera frame, before :attr:`_T_last` moves on) as it is carried into the next
        prediction. Where the frame held its weakest translation (``axis_w``, world) less than
        ``weak_step_strength`` and its step along it differs from the last carried one (scaled to this frame's
        time) by more than ``weak_step_mm``, that difference is not carried ("hold"), or is taken back from the
        next prediction too ("undo"). Sets :attr:`_weak_mm` (how far the carried step moved)."""
        p = self.params
        if p.weak_step_strength is None or not strength < p.weak_step_strength or self._last_step is None:
            return step
        R_s, s_prev, dt_prev = self._last_step
        R_last = self._T_last[:3, :3]
        prev_w = (R_last @ R_s.T) @ s_prev  # the last carried step, in the world
        if dt and dt_prev:
            prev_w = prev_w * (float(dt) / float(dt_prev))
        a = np.asarray(axis_w, dtype=np.float64)
        a = a / max(float(np.linalg.norm(a)), 1e-12)
        s_w = R_last @ step
        d = float((s_w - prev_w) @ a)
        if not abs(d) > p.weak_step_mm:
            return step
        f = 2.0 if p.weak_step_mode == "undo" else 1.0
        self._weak_mm = abs(f * d)
        return R_last.T @ (s_w - f * d * a)

    def _fail(self, reason: str, t: float | None, info: dict, t0: float, timings: dict) -> TrackResult:
        if self._initialized:
            self._n_lost += 1
        info = dict(info)
        info["reason"] = reason
        info["lost_frames"] = self._n_lost
        log.debug("markerless frame %d failed: %s", self._frame, reason)
        return self._finish(TrackResult(self._T_last.copy(), False, info), t, t0, timings)

    def _finish(self, res: TrackResult, t: float | None, t0: float, timings: dict) -> TrackResult:
        timings["total"] = 1e3 * (time.perf_counter() - t0)
        res.info["timings_ms"] = timings
        self.trajectory.append((float("nan") if t is None else float(t), res.T_wc.copy(), bool(res.ok)))
        self.last_result = res
        return res

    def integrate(self, depth_mm: np.ndarray, intensity: np.ndarray | None, T_wc: np.ndarray,
                  t: float | None = None) -> None:
        """Fuse a depth frame at an externally supplied pose (e.g. from the marker tracker).

        The pose also becomes the tracker's last good pose, so :meth:`track` can continue from it.
        If ``t`` is given the pose is appended to :attr:`trajectory`.
        """
        depth = self._clean_depth(depth_mm)
        T = np.asarray(T_wc, dtype=np.float64).reshape(4, 4)
        self.flush_pending()
        self._integrate_cleaned(depth, T)
        self._initialized = True
        self._accept(T, t, consecutive=False)
        if t is not None:
            self.trajectory.append((float(t), T.copy(), True))

    def _weight(self, weight_threshold: float | None) -> float:
        thr = self.params.extract_weight_threshold if weight_threshold is None else float(weight_threshold)
        # Open3D keeps voxels whose weight is strictly above the threshold: cap it below the number
        # of fused frames, otherwise a young model (< 4 frames) extracts nothing and Open3D raises
        return float(min(thr, max(self._n_integrated - 1, 0)))

    def _grid_for_extraction(self) -> Any:
        """The Open3D grid to mesh: the model itself, or a CPU copy of the GPU volume's observed bricks."""
        self.flush_pending()
        if self._gpu is not None:
            return self._gpu.to_open3d(self._o3d, self._o3c) if self._n_integrated else None
        return self._vbg if self.n_blocks else None

    def extract_mesh(self, weight_threshold: float | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Marching-cubes mesh of the TSDF: ``(V (N,3) mm, F (M,3) int64, N (N,3) outward normals)``.

        GPU backend: only what is inside the rolling cube (a CPU copy of it is meshed by Open3D).
        """
        vbg = self._grid_for_extraction()
        if vbg is None:
            return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros((0, 3))
        mesh = vbg.extract_triangle_mesh(weight_threshold=self._weight(weight_threshold))
        V = mesh.vertex.positions.cpu().numpy().astype(np.float64)
        if len(V) == 0:
            return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros((0, 3))
        F = mesh.triangle.indices.cpu().numpy().astype(np.int64)
        N = mesh.vertex.normals.cpu().numpy().astype(np.float64)
        return V, F, N

    def extract_points(self, weight_threshold: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Zero-crossing points of the TSDF with normals: ``(P (N,3) mm, N (N,3))`` (GPU: inside the cube)."""
        vbg = self._grid_for_extraction()
        if vbg is None:
            return np.zeros((0, 3)), np.zeros((0, 3))
        pcd = vbg.extract_point_cloud(weight_threshold=self._weight(weight_threshold))
        if pcd.is_empty():
            return np.zeros((0, 3)), np.zeros((0, 3))
        P = pcd.point.positions.cpu().numpy().astype(np.float64)
        N = pcd.point.normals.cpu().numpy().astype(np.float64)
        return P, N
