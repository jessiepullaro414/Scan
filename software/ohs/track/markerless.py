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
  asynchronous.
* ``"auto"`` (default): ``"gpu"`` when :func:`ohs.compute.get_device` gives a GPU (and the volume
  can be created there), else ``"open3d"``. ``"gpu"`` raises when no GPU is available.

Open3D notes (0.20, verified empirically):

* everything is in **mm**: ``depth_scale=1``, ``voxel_size`` and depth limits in mm; extrinsics
  are ``T_cw`` in mm;
* ``integrate`` truncates the projected pixel coordinate; the integration intrinsics are shifted
  by +0.5 px so the lookup becomes round-to-nearest (removes a ~0.35 mm bias on tilted surfaces);
* the first ``ray_cast`` after the map grows uses an undersized range-map fragment buffer and
  misses most of the image; a throw-away warm-up cast is done whenever the block count grew by
  more than ``warmup_growth``;
* ``ray_cast`` hangs when ``range_map_down_factor`` does not divide the image size (a divisor is
  chosen automatically);
* ray-cast ``vertex``/``normal`` are in the camera frame and normals point *away* from the camera
  (flipped here); ``hashmap().key_tensor()`` includes inactive slots (only active keys are used).

The public API is in mm; :meth:`extract_mesh` returns outward normals and counter-clockwise faces.
The ``intensity`` image is accepted for API symmetry but not used: the projected dot pattern moves
with the scanner, so photometric alignment on these images would bias the motion towards zero.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ohs.geometry import invert_se3, make_se3, rotation_angle_deg, skew, so3_exp, so3_log
from ohs.types import TrackResult

__all__ = ["FusionParams", "MarkerlessTracker", "MODEL_BACKENDS"]

log = logging.getLogger(__name__)

MODEL_BACKENDS = ("auto", "open3d", "gpu")
SLIDE_SV = 0.8  # the weak subspace holds a translation this close to a pure one: "slide"
TURN_SV = 0.5  # ... a rotation (about any axis, e.g. through the scene) at least this strongly: "turn"

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
    # model ray casting
    raycast_scale: float = 0.5  # model maps rendered at this fraction of the input resolution
    raycast_weight_threshold: float = 3.0  # capped by the number of integrated frames
    warmup_growth: float = 0.03  # warm-up ray cast when the block count grew by more than 3 %
    model_normal_window: int = 5  # box-average model normals over k x k ray-cast pixels (1 = off)
    extract_weight_threshold: float = 3.0
    # ICP
    # source subsampling per level (coarse -> fine), in pixels of a 1280 px wide image (scaled with the width).
    # The finest level samples every 8th pixel at 1280 px, not every 4th:
    # neighbouring points share the camera-fixed dot error, so the denser sampling was 3-4 times slower and no
    # more accurate (VALIDATION.md, "GPU tuning")
    icp_strides: tuple[int, ...] = (16, 8, 8)
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
        if str(self.model_backend).strip().lower() not in MODEL_BACKENDS:
            raise ValueError(f"model_backend must be one of {MODEL_BACKENDS}, got {self.model_backend!r}")
        if int(self.gpu_volume_voxels) < 64 or int(self.gpu_volume_voxels) % 16:
            raise ValueError(f"gpu_volume_voxels must be a multiple of 16, >= 64, got {self.gpu_volume_voxels!r}")
        if self.gpu_border_mm < 0 or self.gpu_weight_threshold < 0:
            raise ValueError("gpu_border_mm and gpu_weight_threshold must be >= 0")

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
    """Refined ray cast of the model at pose ``T_wc`` (maps in that camera's frame)."""

    T_wc: np.ndarray
    V: np.ndarray  # (h*w, 3) float64 vertices
    N: np.ndarray  # (h*w, 3) float64 unit normals, towards the camera
    valid: np.ndarray  # (h*w,) bool
    K: np.ndarray  # intrinsics of the maps
    size: tuple[int, int]  # (w, h)

    @property
    def n_valid(self) -> int:
        return int(self.valid.sum())


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


def _weak_directions(J: np.ndarray, Q: np.ndarray, ratio: float) -> tuple[float, list[dict]]:
    """Weak motions of a point-to-plane problem: ``(weakest / strongest eigenvalue, [{"kind", "axis"}])``.

    ``J`` (n,6) has rows ``[q x n, n]`` and ``Q`` (n,3) the points (camera frame, mm). The eigen-
    directions below ``ratio`` of the strongest span the weak subspace. It holds a ``"slide"`` when a
    translation lies (almost) in it, and a ``"turn"`` when a rotation does, about any axis: a
    sphere's, or a flat panel's normal. ``axis`` is that translation's direction or that rotation's
    axis, in the camera frame. Weak eigenvectors mix freely (a panel's two slides and its turn), so
    the subspace is classified, not each eigenvector.
    """
    S = _scene_scale(Q)
    Js = J / S
    ev, X = np.linalg.eigh(Js.T @ Js)
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

        self._o3d, self._o3c = o3d, o3c
        self.params = params if params is not None else FusionParams()
        self.K = np.asarray(K, dtype=np.float64).reshape(3, 3).copy()
        self.image_size = (int(image_size[0]), int(image_size[1]))
        self.device = _resolve_device(self.params.device)
        w, h = self.image_size
        p = self.params
        self._gpu: Any = None  # GpuTsdfVolume when the model is on the GPU
        self._vbg: Any = None
        self._gpu_status = self._make_gpu_volume(str(p.model_backend).strip().lower())
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
        # the strides are set for 1280 px wide images and scale with the width, so every resolution
        # samples about as many points (a 640 px preview: every 4th pixel at the finest level)
        self._strides = [max(1, int(round(s * w / 1280.0))) for s in p.icp_strides]
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
        log.info("MarkerlessTracker: %dx%d, voxel %.2f mm, trunc %.2f mm, model %s (%s), ray cast %dx%d",
                 w, h, p.voxel_mm, p.trunc_mm, self.model_backend, self.model_device, *self._ray_size)
        self.reset()

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
        try:
            self._gpu = GpuTsdfVolume(dev, p.voxel_mm, p.trunc_mm, p.depth_min_mm, p.depth_max_mm,
                                      edge_voxels=int(p.gpu_volume_voxels), border_mm=float(p.gpu_border_mm))
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
        self._warm_blocks = 0
        self._initialized = False
        self._T_last = np.eye(4)  # last good pose
        self._t_last: float | None = None
        self._last_step: tuple[np.ndarray, np.ndarray, float | None] | None = None  # (R, t, dt)
        self._R_acc = np.eye(3)  # gyro rotation accumulated since the last good frame
        self._n_lost = 0
        self._frame = 0
        self.last_result: TrackResult | None = None

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
        bad = ~np.isfinite(d) | (d < p.depth_min_mm) | (d > p.depth_max_mm)
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
        fx = self.K[0, 0]
        for o, s, rays in self._src_levels:
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
        thr = self.params.gpu_weight_threshold if self._gpu is not None else self.params.raycast_weight_threshold
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
        depth = maps[:, 0]
        valid = depth > 0
        V = self._rays_ray * depth[:, None].astype(np.float64)
        N = maps[:, 1:4].astype(np.float64)
        w, h = self._ray_size
        return _ModelView(np.asarray(T_wc, dtype=np.float64).copy(), V, N, valid, self._K_ray, (w, h))

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
        with o3d.utility.VerbosityContextManager(o3d.utility.VerbosityLevel.Error):
            if n_blocks > self._warm_blocks * (1.0 + p.warmup_growth):
                # grows Open3D's range-map fragment buffer; the first cast after growth is incomplete
                self._vbg.ray_cast(keys, self._K_ray_t, ext, w, h, ["depth"], *args)
                self._warm_blocks = n_blocks
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
        if p.model_normal_window > 1:
            N, valid = self._smooth_normals(N, valid, w, h, p.model_normal_window)
        return _ModelView(T_wc.copy(), V, N, valid, self._K_ray, (w, h))

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
             rot_sigma_rad: float, extra_coarse: bool) -> _IcpOutput:
        """Projective point-to-plane Gauss-Newton; returns the pose of the frame in the view frame."""
        p = self.params
        K = view.K
        w, h = view.size
        tV, tN, tvalid = view.V, view.N, view.valid
        sig2 = p.icp_sigma_mm ** 2
        lam_r = sig2 / rot_sigma_rad ** 2
        lam_t = sig2 / p.trans_sigma_mm ** 2
        R, t = np.eye(3), np.zeros(3)
        levels = [(P, N, n_it, dmax) for (P, N), n_it, dmax in zip(src, p.icp_iterations, p.icp_max_dist_mm)]
        if extra_coarse:
            P0, N0 = src[0]
            levels = [(P0[::4], N0[::4], 2 * p.icp_iterations[0], p.icp_max_dist_mm[0] * 1.5)] + levels
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
            Q, Vt, Nt = Q[sel], tV[pix], tN[pix]
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
            out.min_eig_ratio, out.weak = _weak_directions(J, Q, p.degenerate_eig_ratio)
        return out

    # ------------------------------------------------------------------ public API
    def track(self, depth_mm: np.ndarray, intensity: np.ndarray | None = None, t: float | None = None,
              R_prior: np.ndarray | None = None) -> TrackResult:
        """Track one depth frame against the model and fuse it on success.

        ``depth_mm`` (H,W) float mm, 0/NaN invalid; ``t`` camera timestamp (s, optional, used for
        the constant-velocity model); ``R_prior`` gyro rotation from the previous call's frame to
        this one (``v_prev = R_prior @ v_cur``), or ``None``.
        """
        t0 = time.perf_counter()
        p = self.params
        self._frame += 1
        depth = self._clean_depth(depth_mm)
        n_valid_px = int(np.count_nonzero(depth))
        timings: dict[str, float] = {}

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
        timings["icp"] = 1e3 * (time.perf_counter() - tc)
        T_est = view.T_wc @ res.T_delta
        reason = self._gate(res, T_pred, T_est, widen)
        D = invert_se3(T_pred) @ T_est
        info.update({
            "fitness": res.fitness, "rmse_mm": res.rmse, "n_inliers": res.n_inliers, "n_points": res.n_points,
            "n_model_px": view.n_valid, "iterations": res.iterations,
            "jump_mm": float(np.linalg.norm(D[:3, 3])), "jump_deg": rotation_angle_deg(D[:3, :3]),
            "min_eig_ratio": res.min_eig_ratio, "degenerate": res.min_eig_ratio < p.degenerate_eig_ratio,
            "weak": res.weak,
        })
        if reason is not None:
            return self._fail(reason, t, info, t0, timings)
        ti = time.perf_counter()
        self._integrate_cleaned(depth, T_est)
        timings["integrate"] = 1e3 * (time.perf_counter() - ti)
        if lost:
            log.info("markerless: relocalised after %d lost frames (%s, fitness %.2f)", lost, name, res.fitness)
        self._accept(T_est, t, consecutive=(lost == 0))
        return self._finish(TrackResult(T_est.copy(), True, info), t, t0, timings)

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

    def _accept(self, T_wc: np.ndarray, t: float | None, consecutive: bool | None) -> None:
        if consecutive:
            D = invert_se3(self._T_last) @ T_wc
            dt = None if (t is None or self._t_last is None) else float(t - self._t_last)
            self._last_step = (D[:3, :3].copy(), D[:3, 3].copy(), dt)
        else:
            self._last_step = None
        self._T_last = np.asarray(T_wc, dtype=np.float64).copy()
        self._t_last = t
        self._R_acc = np.eye(3)
        self._n_lost = 0

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
