"""Loop closure and global pose-graph optimisation of a tracked scan ("Straighten" in Studio).

Frame-to-model tracking (:mod:`ohs.track.markerless`) drifts slowly, and nothing in the tracker
removes the drift. When a long scan comes back over a surface it saw before (going around a part,
or sweeping back and forth over a panel), the two passes then disagree: doubled or blurred
surfaces, wrong measurements. :func:`straighten_trajectory` finds such revisits, measures how far
the tracked poses are off there, and bends the trajectory slightly so that the passes line up. It
works on a finished scan (the recording plus the ``trajectory.csv`` of
:func:`ohs.app.process.process_recording`) and returns corrected poses for a new fusion.

Method
------
1. **Keyframes** (:func:`select_keyframes`): tracked frames spaced by camera travel
   (``kf_travel_mm``), rotation (``kf_rotation_deg``) or time (``kf_max_gap_s``). The first and the
   last tracked frame are keyframes, and so are the frames on both sides of a tracking failure, so
   a relocalisation jump sits in one odometry edge. Scans that would need more than
   ``max_keyframes`` get a wider spacing.
2. **Loop candidates** (:func:`find_loop_candidates`): keyframe pairs whose look-at points
   (``view_distance_mm`` along the optical axis) are close (``loop_max_dist_mm``) and whose view
   directions agree (``loop_max_angle_deg``), but which lie far apart along the scan (at least
   ``loop_min_travel_mm`` of camera travel and ``loop_min_keyframes`` keyframes in between). The
   best ones by distance and angle are kept, at most one per ``candidate_spacing`` keyframes on
   both ends, at most ``max_candidates``.
3. **Keyframe clouds** (:class:`KeyframeClouds`, only for the keyframes of a candidate): the raw
   frame is rectified at ``cloud_width_px`` (half resolution of the 1280 px camera), depth comes
   from :class:`~ohs.vision.depth.DepthEstimator` (the GPU when there is one), and the depth is
   smoothed with the tracking filter (:func:`ohs.app.pipeline.tracking_depth`). The filter matters:
   stereo depth carries a dot-scale error pattern fixed to the camera, which would pull ICP
   between two views towards "the camera did not move" (see VALIDATION.md, finding 1). Points are
   back-projected on a grid of about 1.4 samples per ``voxel_mm`` at 250 mm, with normals from
   central differences of the smoothed depth (towards the camera), and voxel-averaged at
   ``voxel_mm`` plus a ``coarse_voxel_mm`` copy.
4. **Verification** (:func:`verify_loop`): point-to-plane Gauss-Newton ICP from the relative pose
   the tracked trajectory gives, in three levels: coarse cloud at 1.5x the plausible drift between
   the two keyframes (``drift_frac`` of the camera travel between them plus ``drift_slack_mm``, at
   most ``max_drift_mm``; the correspondence distance is clipped to 10-30 mm), then the fine cloud
   at ``2 * icp_fine_mm`` and at ``icp_fine_mm``. Matches need normals within 60 deg; Tukey
   weights take their scale from the residuals (4.685 robust sigmas, at least ``robust_min_mm``), so
   pairs across depth edges stop pulling once the clouds are close.
   **Only measurable directions move.** Each step solves the normal equations along the
   eigen-directions above ``weak_rel`` of the strongest (rotations scaled to mm at the scene
   distance), and at the end the whole motion from the guess is projected onto the fine level's
   strong directions and polished inside them. On a plane, a sphere or a body of revolution the
   unmeasurable motions (sliding along the table, turning about the sphere's axis) keep the guess.
   Without this, ICP from the *true* pose slid 4-18 mm or 5-15 deg along them in simulation (the
   bracket on its table, the sphere), and those slides were accepted as loops.
   A loop is accepted when, at ``icp_fine_mm``, enough points match (``min_inliers``), both clouds
   overlap (``min_overlap``: the matched fraction of each), the matched surfaces agree along their
   normals (point-to-plane RMS ``max_rmse_mm``) and ICP moved the tracked relative pose no more
   than the plausible drift (RMS movement of the matched points; ``max_drift_deg``).
   **Information.** Its information matrix is the point-to-plane ``J^T J / n`` of the matches,
   ``J = [p x n, n]`` (``p`` in the source keyframe's frame, ``n`` the target normal rotated into
   it), restricted to the measurable directions and divided by ``loop_sigma_mm^2`` (how well a
   loop pins its points along their normals; on perfect simulated poses verified loops were off by
   0.03-0.08 mm RMS). Open3D's ``get_information_matrix_from_point_clouds`` is the point-to-point
   version of the same sum: it claims full information along the unmeasurable directions, where
   the loop only repeats the drifted guess. ``information="open3d"`` uses it instead (computed in
   the same source frame). On six simulated drift cases (bracket and sphere orbits, panel sweeps)
   it closed the gap along the normals as well, but left the trajectory 0.2-0.5 mm RMS farther
   from the truth in five of them (in two, farther than before straightening) and the passes'
   sideways offset up to 43 % larger.
5. **Pose graph** (:func:`optimize_pose_graph`): one node per keyframe at its tracked pose;
   odometry edges between consecutive keyframes carry the tracked relative pose with the
   information of a random-walk drift model (``drift_mm_per_frame``, ``drift_deg_per_frame`` per
   processed frame between them, lost frames counted 5x); loop edges are marked uncertain. Open3D's
   ``global_optimization`` (Levenberg-Marquardt) solves it with the line process of Choi et al.
   2015, which down-weights and then prunes loops that disagree with the rest. The pruning
   threshold is graduated: the first pass accepts every verified loop (so that the true drift can
   be closed at all), the last prunes loops still more than ``loop_prune_mm`` (RMS along the
   normals) off. The first keyframe keeps its pose exactly (it defines the world frame).
6. **Never worse.** The optimised poses are used only when (a) the loops were off by more than
   ``min_residual_mm`` to begin with (below that the scan already lines up within the measurement
   noise, and is returned unchanged), (b) the loops' summed squared residual at least halves
   (``min_improvement``) and (c) no odometry edge changes by more than the drift model allows
   (chi-square per edge at most ``max_odometry_chi2``). If (b) or (c) fails with several loops, the
   worst loop is dropped and the graph solved again (``max_retries``). Otherwise the input poses are
   returned unchanged, and the verdict says why.
   **Large drift** (research plan 4.4.1; ``global_registration``, ``adaptive_drift``). Three
   additions let it close drift far beyond ICP's reach from the tracked pose:

   - the candidate gate widens with the camera travel between the two keyframes
     (``global_drift_frac`` of it plus the slack, at most ``global_max_drift_mm``);
   - a candidate that fails verification is checked again against those wider bounds
     (``global_max_drift_mm`` / ``global_max_drift_deg``): first by ICP from the tracked pose with
     a wider reach, then from a global first guess (:func:`global_match`: FPFH features of the
     coarse clouds matched by Fast Global Registration, else by RANSAC; a guess farther from the
     tracked pose than the bounds allow is skipped). From a global guess the unmeasurable
     directions still keep the tracked pose. Such a loop counts only when at least
     ``global_min_loops`` loops pass in all, so one wrong large match can't bend a scan alone;
   - the drift model is scaled to what the loops show: by the median, over the loops, of how far
     ICP moved them against the random walk's spread over their frames (between 1 and
     ``max_drift_scale``; ``drift_scale`` in the report). Without that, the graph's line process
     takes loops that need more bending than the default model as disagreeing, and prunes them.
7. **Corrections** (:func:`interpolate_poses`): each keyframe's correction ``C = T_opt @
   inv(T_in)`` (a rigid change of the world around it) is applied to every tracked frame from the
   two keyframes around it, and the two results are blended by time (rotation slerp, translation
   lerp). So every frame keeps its tracked pose relative to its neighbouring keyframes.

Frames and units
----------------
Poses are ``T_wc`` (frame C -> world, mm), as in ``trajectory.csv``; the world is whatever the
trajectory uses (the camera of the first tracked frame in markerless mode). Keyframe clouds are in
their keyframe's frame C at the cloud resolution (mm). A loop edge ``(a, b)`` carries ``X =
inv(T_b) @ T_a`` (points of keyframe ``a`` into keyframe ``b``'s frame), Open3D's convention, and
its residual ``X^-1 inv(T_b) T_a`` is a small motion of ``a``'s frame: rotation (rad) first, then
translation (mm), which is the order of the information matrices. Loop residuals in mm are
``sqrt(e^T (J^T J / n) e)``: the RMS displacement of the matched points along their normals.

Report
------
``keyframes``, ``loop_candidates``, ``loops_accepted``, ``loops`` (frame index pairs),
``correction_mm`` / ``correction_deg`` (the largest change of any tracked frame's camera position
and orientation), ``verdict`` (plain words), ``changed`` (poses differ from the input),
``reason`` (``straightened``, ``too_short``, ``no_candidates``, ``not_verified``, ``disagree``,
``already_straight``, ``not_better``, ``bends_too_much``), ``candidates`` (per candidate: frames,
accepted, why not, ``large`` (checked against the large-drift bounds), ``via`` (``tracked`` or
``global``: where ICP started), fitness, overlap, RMS, how far ICP moved, measurable directions
``dof``, kept by the graph), ``drift_scale`` (step 4, large drift), ``residual_mm`` (loops
before / after, largest odometry chi-square), ``timings_s``,
``compute`` (where the depth ran) and, when the recording has true poses, ``ground_truth``:
``before`` / ``after`` (:func:`ohs.app.process.trajectory_errors` summaries, aligned at the first
frame) and ``revisits`` (at the candidates' frame pairs: ``gap_mm``, the distance between the two
passes' surfaces along their normals, i.e. doubled surfaces; ``offset_mm``, how far the observed
point lands in any direction, sliding included; ``rot_deg``).

Speed (Ryzen 7 7735U + Radeon 680M): a 1-minute 1280x800 scan (720 deg around the bracket, 105
keyframes, 48 candidates) takes about 8 s: clouds 4.2 s (reading 1.2, depth 1.5, points 1.5),
ICP 3.8 s, pose graph 0.2 s. The cost follows the keyframes and candidates, not the frame count.

Known limits
------------
* **Loops measure only what the surfaces fix.** On plane-dominated views (a part on a table, a
  body panel) a loop fixes the distance to the surface and its tilt, which is what doubles
  surfaces, but not sliding along it; around a sphere or a body of revolution not the turn about
  its axis. Point-to-plane ICP does not use a panel's outline either (all its normals point the
  same way). Tracking drifts mostly along those same directions (same geometry), so straightening
  often finds the passes already lined up: on a simulated car-panel sweep and sphere orbit the real
  pipeline's 2-3 mm and 0.7 mm of drift lay along them, and the gap at the revisits was under
  0.1 mm.
* **Consistency, not the truth.** The first frame defines the world, and drift before the first
  loop keyframe cannot be observed: the gap at the revisits drops about tenfold, the error against
  the truth (from the first frame) much less, and in single runs its maximum can grow by a few
  tenths of a mm along unmeasured directions.
* **Large drift** closes the doubled surfaces, not the sliding: on simulated orbits with 5 to 20
  times the usual drift, the gap between the passes along their normals fell 3-18 times, but they
  still slid past each other by about as much as before (with steady drift up to 17 % more), and
  the trajectory's error against the truth fell much less (VALIDATION.md, "Straighten for large
  drift"). Drift beyond ``global_max_drift_mm`` or the candidate gate is not found at all.
* One wrong loop that passes verification and stays within the plausible drift cannot be told
  from a right one; the line process and the never-worse rule only catch loops that disagree with
  other loops or need more bending than drift explains.
* Only the keyframes' relative poses are corrected; drift between two keyframes that is not
  linear in time stays (keyframes are close: ``kf_travel_mm`` / ``kf_rotation_deg``).
* The frame-to-model tracker already re-registers against its model when it comes back to a
  surface still in it (the GPU model is a 768 mm rolling cube, the Open3D model keeps the whole
  scan); the loops then find little left to correct.
"""
from __future__ import annotations

import csv
import logging
import math
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from ohs.geometry import invert_se3, rotation_angle_deg, so3_exp, so3_log

__all__ = [
    "StraightenParams",
    "TrajectoryRow",
    "LoopCheck",
    "KeyframeClouds",
    "straighten_trajectory",
    "read_trajectory",
    "write_trajectory",
    "select_keyframes",
    "find_loop_candidates",
    "verify_loop",
    "global_match",
    "OdometryEdge",
    "odometry_edges",
    "optimize_pose_graph",
    "interpolate_poses",
    "loop_residual_mm",
]

log = logging.getLogger(__name__)

TrajectoryRow = tuple[int, float, bool, np.ndarray]  # (frame index, t_host, ok, T_wc 4x4) as in trajectory.csv
INFORMATION_KINDS = ("point_to_plane", "open3d")
LOST_FRAME_WEIGHT = 5.0  # a lost frame counts this many tracked frames in the odometry drift model


@dataclass
class StraightenParams:
    """Settings of :func:`straighten_trajectory` (lengths in mm, angles in degrees; module docstring)."""

    # 1. keyframes: a new one after this much camera travel, rotation or time
    kf_travel_mm: float = 30.0
    kf_rotation_deg: float = 10.0
    kf_max_gap_s: float = 2.0
    max_keyframes: int = 300
    # 2. loop candidates
    view_distance_mm: float = 250.0
    loop_max_dist_mm: float = 70.0
    loop_max_angle_deg: float = 35.0
    loop_min_travel_mm: float = 150.0
    loop_min_keyframes: int = 4
    max_candidates: int = 48
    candidate_spacing: int = 2
    # 3. keyframe clouds
    cloud_width_px: int = 640
    voxel_mm: float = 2.0
    coarse_voxel_mm: float = 4.0
    filter_px: float | None = None  # tracking_depth sigma; None: the pipeline's default for the resolution
    min_cloud_points: int = 1000
    # 4. verification
    icp_iterations: tuple[int, int, int] = (30, 15, 10)
    icp_fine_mm: float = 3.0
    min_inliers: int = 1000
    min_overlap: float = 0.3
    max_rmse_mm: float = 0.8
    drift_frac: float = 0.03
    drift_slack_mm: float = 3.0
    max_drift_mm: float = 20.0
    max_drift_deg: float = 5.0
    robust_min_mm: float = 1.0  # smallest Tukey scale of the ICP weights
    weak_rel: float = 3e-3  # directions with less than this fraction of the strongest constraint are left alone
    information: str = "point_to_plane"  # or "open3d" (get_information_matrix_from_point_clouds)
    loop_sigma_mm: float = 0.15  # how well a loop pins its matched points (RMS along the normals)
    # 5. pose graph
    drift_mm_per_frame: float = 0.2
    drift_deg_per_frame: float = 0.05
    loop_prune_mm: float = 0.5
    # 6. never worse
    min_residual_mm: float = 0.2
    min_improvement: float = 0.5  # the loops' summed squared residual must at least halve
    max_odometry_chi2: float = 36.0
    max_retries: int = 2
    # large drift (research plan 4.4.1): a wider candidate gate, a global first guess (FPFH + FGR on
    # the coarse clouds) where ICP from the tracked pose fails, and a drift model scaled to what the
    # loops measure
    global_registration: bool = True
    global_drift_frac: float = 0.15  # plausible drift for a global loop: this share of the travel, plus slack
    global_max_drift_mm: float = 120.0
    global_max_drift_deg: float = 20.0
    global_min_loops: int = 2  # a loop beyond the ordinary drift bound counts only with this many loops in all
    fpfh_radius_mm: float = 20.0
    fgr_max_corr_mm: float = 6.0
    adaptive_drift: bool = True
    max_drift_scale: float = 30.0

    def __post_init__(self) -> None:
        if self.information not in INFORMATION_KINDS:
            raise ValueError(f"information must be one of {INFORMATION_KINDS}, got {self.information!r}")
        if len(self.icp_iterations) != 3:
            raise ValueError("icp_iterations needs three levels (coarse, middle, fine)")
        for name in ("kf_travel_mm", "kf_rotation_deg", "kf_max_gap_s", "voxel_mm", "coarse_voxel_mm", "icp_fine_mm",
                     "loop_sigma_mm", "drift_mm_per_frame", "drift_deg_per_frame", "loop_prune_mm"):
            if not float(getattr(self, name)) > 0:
                raise ValueError(f"{name} must be positive")


# --------------------------------------------------------------------------- trajectory.csv
def read_trajectory(path: str | Path) -> list[TrajectoryRow]:
    """Rows ``(index, t_host, ok, T_wc)`` of a ``trajectory.csv`` written by ``process_recording``
    (columns ``index, t, ok, T00..T33``, ``T_wc`` row-major)."""
    rows: list[TrajectoryRow] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            T = np.array([float(r[f"T{a}{b}"]) for a in range(4) for b in range(4)], dtype=np.float64).reshape(4, 4)
            rows.append((int(r["index"]), float(r["t"]), bool(int(r["ok"])), T))
    return rows


def write_trajectory(path: str | Path, rows: Sequence[TrajectoryRow]) -> None:
    """Write rows in the ``trajectory.csv`` format of ``process_recording`` (readable by :func:`read_trajectory`)."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "t", "ok"] + [f"T{r}{c}" for r in range(4) for c in range(4)])
        for i, t, ok, T in rows:
            w.writerow([int(i), f"{float(t):.6f}", int(bool(ok))] + [f"{x:.9g}" for x in np.asarray(T).reshape(16)])


def _as_rows(trajectory: Sequence[Sequence[Any]] | str | Path) -> list[TrajectoryRow]:
    """Rows sorted by frame index, poses as float64 copies; a row with a non-finite pose counts as lost."""
    if isinstance(trajectory, (str, Path)):
        trajectory = read_trajectory(trajectory)
    rows: list[TrajectoryRow] = []
    for r in trajectory:
        i, t, ok, T = r
        T = np.asarray(T, dtype=np.float64).reshape(4, 4)
        rows.append((int(i), float(t), bool(ok) and bool(np.all(np.isfinite(T))), T.copy()))
    rows.sort(key=lambda r: r[0])
    return rows


# --------------------------------------------------------------------------- small SE(3) helpers
def _vec6(E: np.ndarray) -> np.ndarray:
    """(rotation vector, translation) of a small motion ``E`` (the residual of an edge)."""
    return np.concatenate([so3_log(E[:3, :3]), E[:3, 3]])


def _rel(T: np.ndarray, a: int, b: int) -> np.ndarray:
    """``inv(T[b]) @ T[a]``: points of ``a``'s frame into ``b``'s frame."""
    return invert_se3(T[b]) @ T[a]


def _slerp(Ra: np.ndarray, Rb: np.ndarray, alpha: float) -> np.ndarray:
    return Ra @ so3_exp(alpha * so3_log(Ra.T @ Rb))


# --------------------------------------------------------------------------- 1. keyframes
def _keyframes(T: np.ndarray, t: np.ndarray, gap: np.ndarray, d_mm: float, d_deg: float, d_s: float) -> list[int]:
    sel = [0]
    a = 0
    cos_lim = math.cos(math.radians(min(d_deg, 179.0)))
    for m in range(1, len(T)):
        if gap[m]:
            if sel[-1] != m - 1:
                sel.append(m - 1)
            sel.append(m)
            a = m
            continue
        moved = float(np.linalg.norm(T[m, :3, 3] - T[a, :3, 3]))
        cos_th = 0.5 * (float(np.trace(T[a, :3, :3].T @ T[m, :3, :3])) - 1.0)
        if moved >= d_mm or cos_th <= cos_lim or t[m] - t[a] >= d_s:
            sel.append(m)
            a = m
    if sel[-1] != len(T) - 1:
        sel.append(len(T) - 1)
    return sel


def select_keyframes(rows: Sequence[TrajectoryRow], travel_mm: float = 30.0, rotation_deg: float = 10.0,
                     max_gap_s: float = 2.0, max_keyframes: int = 300) -> list[int]:
    """Positions (into ``rows``) of the keyframes among the tracked rows (module docstring, step 1).

    The spacing grows in 25 % steps until at most ``max_keyframes`` are needed (the frames around
    tracking failures are always kept, so very broken trajectories can still exceed it).
    """
    ok_pos = [k for k, r in enumerate(rows) if r[2]]
    if not ok_pos:
        return []
    T = np.stack([np.asarray(rows[k][3], dtype=np.float64) for k in ok_pos])
    t = np.array([float(rows[k][1]) for k in ok_pos])
    gap = np.zeros(len(ok_pos), dtype=bool)
    gap[1:] = np.diff(np.asarray(ok_pos)) > 1
    f = 1.0
    while True:
        sel = _keyframes(T, t, gap, travel_mm * f, rotation_deg * f, max_gap_s * f)
        if len(sel) <= max(2, int(max_keyframes)) or f > 100.0:
            break
        f *= 1.25
    return [ok_pos[m] for m in sel]


def _cumulative_travel(rows: Sequence[TrajectoryRow]) -> np.ndarray:
    """Camera path length (mm) from the first tracked row to every row (failed rows repeat the last value)."""
    out = np.zeros(len(rows))
    last: np.ndarray | None = None
    acc = 0.0
    for k, (_, _, ok, T) in enumerate(rows):
        if ok:
            c = np.asarray(T)[:3, 3]
            if last is not None:
                acc += float(np.linalg.norm(c - last))
            last = c
        out[k] = acc
    return out


# --------------------------------------------------------------------------- 2. loop candidates
def find_loop_candidates(T_kf: np.ndarray, travel_kf: np.ndarray,
                         params: StraightenParams | None = None) -> list[tuple[int, int]]:
    """Keyframe pairs ``(a, b)``, ``a < b``, that probably see the same surface from similar
    directions but lie far apart along the scan (module docstring, step 2), best first."""
    p = params if params is not None else StraightenParams()
    T_kf = np.asarray(T_kf, dtype=np.float64)
    K = len(T_kf)
    gap = max(1, int(p.loop_min_keyframes))
    if K <= gap:
        return []
    z = T_kf[:, :3, 2]
    look = T_kf[:, :3, 3] + float(p.view_distance_mm) * z
    ia, ib = np.triu_indices(K, k=gap)
    dist = np.linalg.norm(look[ia] - look[ib], axis=1)
    ang = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", z[ia], z[ib]), -1.0, 1.0)))
    travel = np.asarray(travel_kf, dtype=np.float64)
    max_dist = np.full(len(ia), float(p.loop_max_dist_mm))
    max_ang = np.full(len(ia), float(p.loop_max_angle_deg))
    if p.global_registration:  # the drift the scan may have gathered between the two widens the gate
        extra = np.minimum(p.drift_slack_mm + p.global_drift_frac * (travel[ib] - travel[ia]), p.global_max_drift_mm)
        max_dist += extra
        max_ang += float(p.global_max_drift_deg) * extra / max(float(p.global_max_drift_mm), 1e-9)
    ok = (dist <= max_dist) & (ang <= max_ang) & (travel[ib] - travel[ia] >= p.loop_min_travel_mm)
    ia, ib, dist, ang = ia[ok], ib[ok], dist[ok], ang[ok]
    order = np.argsort(dist / p.loop_max_dist_mm + ang / p.loop_max_angle_deg, kind="stable")
    r = int(p.candidate_spacing)
    out: list[tuple[int, int]] = []
    for k in order:
        a, b = int(ia[k]), int(ib[k])
        if any(abs(a - a2) <= r and abs(b - b2) <= r for a2, b2 in out):
            continue
        out.append((a, b))
        if len(out) >= int(p.max_candidates):
            break
    return out


# --------------------------------------------------------------------------- 3. keyframe clouds
@dataclass
class _Level:
    pts: np.ndarray  # (n,3) frame C of the keyframe, mm
    nrm: np.ndarray  # (n,3) unit normals towards the camera
    tree: Any  # scipy.spatial.cKDTree of pts


@dataclass
class _Cloud:
    fine: _Level
    coarse: _Level
    o3d: Any  # the fine cloud as an Open3D PointCloud, kept only for information="open3d"
    n: int
    feat: Any = None  # (coarse Open3D cloud, its FPFH features), made on first use by a global match


class KeyframeClouds:
    """Depth clouds of recording frames, made on demand and cached by recording position.

    ``depth_params`` defaults to the pipeline's settings for the cloud resolution. The depth runs
    where :class:`~ohs.vision.depth.DepthEstimator` decides (``backend``: the GPU when there is one).
    """

    def __init__(self, reader: Any, calib: Any, params: StraightenParams | None = None, depth_params: Any = None) -> None:
        from ohs.app.pipeline import default_depth_params, default_track_filter_px, effective_scale  # noqa: PLC0415
        from ohs.vision.depth import DepthEstimator  # noqa: PLC0415
        from ohs.vision.rectify import Rectifier  # noqa: PLC0415

        self._o3d = _open3d()
        self.reader = reader
        self.params = p = params if params is not None else StraightenParams()
        raw_w = int(calib.stereo.image_size[0])
        self.scale = min(1.0, float(p.cloud_width_px) / raw_w)
        self.rectifier = Rectifier(calib.stereo, self.scale)
        self.K = self.rectifier.K
        w, h = self.rectifier.image_size
        s_eff = effective_scale(w)
        self.depth_params = depth_params if depth_params is not None else default_depth_params(s_eff)
        self.estimator = DepthEstimator(self.K, self.rectifier.baseline, self.depth_params)
        self.filter_px = float(p.filter_px if p.filter_px is not None else default_track_filter_px(s_eff))
        self.work_size = (max(1, w // 2), max(1, h // 2))
        # back-projection grid: about 1.4 samples per voxel at 250 mm
        self.stride = max(1, int(p.voxel_mm * self.K[0, 0] / 250.0 / 1.4))
        o = self.stride // 2
        vs, us = np.mgrid[o:h:self.stride, o:w:self.stride]
        y = (vs - self.K[1, 2]) / self.K[1, 1]
        x = (us - self.K[0, 2] - self.K[0, 1] * y) / self.K[0, 0]
        self._rays = np.stack([x, y, np.ones_like(x)], axis=-1)
        self._o = o
        self._cache: dict[int, _Cloud | None] = {}
        self.timings_ms: dict[str, float] = {"read": 0.0, "depth": 0.0, "cloud": 0.0}

    @property
    def backend(self) -> str:
        return self.estimator.backend

    @property
    def device(self) -> str | None:
        return self.estimator.device

    def depth(self, pos: int) -> np.ndarray:
        """Filtered depth (mm, 0 = invalid) of the frame at recording position ``pos``."""
        from ohs.app.pipeline import tracking_depth  # noqa: PLC0415

        t0 = time.perf_counter()
        fr = self.reader.frame(pos)
        left, right = self.rectifier.rectify(fr.left, fr.right)
        t1 = time.perf_counter()
        d = self.estimator.compute(left, right)
        if self.filter_px > 0:
            d = tracking_depth(d, self.filter_px, work_size=self.work_size)
        t2 = time.perf_counter()
        self.timings_ms["read"] += 1e3 * (t1 - t0)
        self.timings_ms["depth"] += 1e3 * (t2 - t1)
        return d

    def _points_normals(self, depth: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Back-projected grid points (frame C, mm) and normals towards the camera, from central
        differences of the (smoothed) depth on the sampling grid; points without four valid,
        continuous neighbours are dropped."""
        z = depth[self._o::self.stride, self._o::self.stride].astype(np.float64)
        X = self._rays * z[..., None]
        c = z[1:-1, 1:-1]
        jump = 3.0 * self.stride * c / self.K[0, 0] + 2.0  # allowed step to a neighbour (grazing surfaces)
        ok = c > 0
        for nb in (z[1:-1, 2:], z[1:-1, :-2], z[2:, 1:-1], z[:-2, 1:-1]):
            ok &= (nb > 0) & (np.abs(nb - c) < jump)
        du = (X[1:-1, 2:] - X[1:-1, :-2])[ok]
        dv = (X[2:, 1:-1] - X[:-2, 1:-1])[ok]
        N = np.cross(dv, du)  # towards the camera for a surface facing it (x right, y down, z forward)
        nn = np.linalg.norm(N, axis=1)
        good = nn > 1e-12
        P = X[1:-1, 1:-1][ok][good]
        N = N[good] / nn[good][:, None]
        flip = np.einsum("ij,ij->i", N, P) > 0
        N[flip] *= -1.0
        return P, N

    def get(self, pos: int) -> _Cloud | None:
        """The cloud of the frame at recording position ``pos`` (``None``: too little depth)."""
        if pos in self._cache:
            return self._cache[pos]
        o3d = self._o3d
        p = self.params
        d = self.depth(pos)
        t0 = time.perf_counter()
        P, N = self._points_normals(d)
        cloud: _Cloud | None = None
        if len(P) >= p.min_cloud_points:
            from scipy.spatial import cKDTree  # noqa: PLC0415

            pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(P))
            pc.normals = o3d.utility.Vector3dVector(N)
            fine = pc.voxel_down_sample(float(p.voxel_mm))  # averages points and normals per voxel
            fine.normalize_normals()
            coarse = fine.voxel_down_sample(float(p.coarse_voxel_mm))
            coarse.normalize_normals()
            n = len(fine.points)
            if n >= p.min_cloud_points:
                levels = []
                for pcd in (fine, coarse):
                    pts = np.asarray(pcd.points, dtype=np.float64).copy()
                    tree = cKDTree(pts, balanced_tree=False, compact_nodes=False)
                    levels.append(_Level(pts, np.asarray(pcd.normals, dtype=np.float64).copy(), tree))
                cloud = _Cloud(levels[0], levels[1], fine if p.information == "open3d" else None, n)
        self.timings_ms["cloud"] += 1e3 * (time.perf_counter() - t0)
        self._cache[pos] = cloud
        return cloud


def _open3d() -> Any:
    try:
        import open3d as o3d  # noqa: PLC0415 (optional heavy dependency)
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError("straightening needs open3d (pip install open3d)") from exc
    return o3d


def _as_o3d(cloud: _Cloud) -> Any:
    """The fine level as an Open3D point cloud (kept from the cloud's making, or rebuilt)."""
    if cloud.o3d is None:
        o3d = _open3d()
        pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(cloud.fine.pts))
        pc.normals = o3d.utility.Vector3dVector(cloud.fine.nrm)
        cloud.o3d = pc
    return cloud.o3d


# --------------------------------------------------------------------------- 4. verification
@dataclass
class LoopCheck:
    """One verified (or rejected) loop candidate between keyframes ``a`` (source) and ``b`` (target).

    ``T_ba`` maps points of ``a``'s frame C into ``b``'s; ``info`` (6x6, rotation then translation,
    in ``a``'s frame) weights its residual in the pose graph; ``J2`` is the mean point-to-plane
    ``J^T J`` of the matches, so that ``sqrt(e^T J2 e)`` is the RMS normal offset (mm) a small
    motion ``e`` of ``a`` causes (:func:`loop_residual_mm`).
    """

    a: int
    b: int
    accepted: bool = False
    reason: str = ""
    T_ba: np.ndarray = field(default_factory=lambda: np.eye(4))
    fitness: float = 0.0
    overlap: float = 0.0
    rmse_mm: float = float("inf")
    n_inliers: int = 0
    moved_mm: float = 0.0
    moved_deg: float = 0.0
    bound_mm: float = 0.0
    conditioning: float = 0.0
    dof: int = 0
    info: np.ndarray = field(default_factory=lambda: np.zeros((6, 6)))
    J2: np.ndarray = field(default_factory=lambda: np.zeros((6, 6)))
    J2_full: np.ndarray = field(default_factory=lambda: np.zeros((6, 6)))  # all directions (evaluation)
    large: bool = False  # checked against the large-drift bounds
    via: str = "tracked"  # where ICP started: "tracked" (the tracked relative pose) or "global" (global_match)

    def summary(self) -> dict:
        return {"accepted": bool(self.accepted), "reason": self.reason, "large": bool(self.large), "via": self.via,
                "fitness": round(float(self.fitness), 4),
                "overlap": round(float(self.overlap), 4),
                "rmse_mm": round(float(self.rmse_mm), 4) if math.isfinite(self.rmse_mm) else None,
                "inliers": int(self.n_inliers), "moved_mm": round(float(self.moved_mm), 3),
                "moved_deg": round(float(self.moved_deg), 3), "bound_mm": round(float(self.bound_mm), 2),
                "conditioning": float(f"{self.conditioning:.3g}"), "dof": int(self.dof)}


_NORMAL_COS = 0.5  # matches whose normals differ by more than 60 deg are not used
_KD_WORKERS = 4  # threads per nearest-neighbour query (more only adds start-up cost at these sizes)
_ICP_POINTS = 5000  # source points used per ICP step (the checks use all of them)


def _match(P: np.ndarray, N: np.ndarray, dst: _Level, T: np.ndarray, dist: float) -> tuple[np.ndarray, np.ndarray]:
    """Indices ``(i, j)`` of source points ``P`` (moved by ``T``) and their nearest target point
    within ``dist`` whose normal agrees (``_NORMAL_COS``)."""
    Q = P @ T[:3, :3].T + T[:3, 3]
    d, j = dst.tree.query(Q, k=1, distance_upper_bound=float(dist), workers=_KD_WORKERS)
    i = np.flatnonzero(np.isfinite(d))
    j = j[i]
    ok = np.einsum("ij,ij->i", N[i] @ T[:3, :3].T, dst.nrm[j]) > _NORMAL_COS
    return i[ok], j[ok]


def _scaled_subspace(H: np.ndarray, L: float, weak_rel: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Eigen-decomposition of a (6,6) point-to-plane matrix with rotations scaled to mm at distance
    ``L``: ``(D, eigenvalues, eigenvectors)`` of ``D H D``, ``D = diag(1/L, 1/L, 1/L, 1, 1, 1)``,
    keeping only directions with at least ``weak_rel`` of the largest eigenvalue."""
    D = np.array([1.0 / L] * 3 + [1.0] * 3)
    lam, U = np.linalg.eigh(H * D[:, None] * D[None, :])
    keep = lam > max(float(weak_rel) * float(lam[-1]), 1e-30)
    return D, lam[keep], U[:, keep]


def _exp6(xi: np.ndarray) -> np.ndarray:
    """Inverse of :func:`_vec6`: the motion with rotation vector ``xi[:3]`` and translation ``xi[3:]``."""
    E = np.eye(4)
    E[:3, :3] = so3_exp(xi[:3])
    E[:3, 3] = xi[3:]
    return E


def _normal_system(P: np.ndarray, N: np.ndarray, dst: _Level, T: np.ndarray, dist: float,
                   robust_min_mm: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, float] | None:
    """Weighted point-to-plane normal equations of the source points ``P`` (normals ``N``) against
    ``dst`` at ``T``, for a motion ``xi`` of the source frame (``T @ exp(xi)``; rotation first):
    ``(J^T W J, J^T W r, residuals, L)``, ``L`` the median distance of the matches from the camera."""
    i, j = _match(P, N, dst, T, dist)
    if len(i) < 30:
        return None
    Ps = P[i]
    Nt = dst.nrm[j]
    r = np.einsum("ij,ij->i", Ps @ T[:3, :3].T + T[:3, 3] - dst.pts[j], Nt)
    Ns = Nt @ T[:3, :3]  # target normals in the source frame
    J = np.concatenate([np.cross(Ps, Ns), Ns], axis=1)
    # Tukey weights with a scale from the residuals themselves (4.685 robust sigmas, at least
    # robust_min_mm): wide while far off, tight once aligned, so that matches across edges (a table
    # point paired with a bracket face) stop pulling instead of faking constraints along sliding
    ar = np.abs(r)
    k = max(float(robust_min_mm), 4.685 * 1.4826 * float(np.median(ar)))
    w = np.where(ar < k, (1.0 - (ar / k) ** 2) ** 2, 0.0)
    Jw = J * w[:, None]
    return Jw.T @ J, Jw.T @ r, r, float(np.median(np.linalg.norm(Ps, axis=1)))


def _icp(src: _Cloud, dst: _Cloud, T0: np.ndarray, levels: Sequence[tuple[bool, float, int]],
         weak_rel: float, robust_min_mm: float, polish: int = 6, ref: np.ndarray | None = None) -> np.ndarray:
    """Coarse-to-fine point-to-plane Gauss-Newton ICP from ``T0`` that moves only along the
    directions the matched surfaces constrain. Returns ``T`` (source points into the target frame).

    ``levels``: ``(coarse cloud?, correspondence distance mm, iterations)``. Each step solves the
    normal equations of a motion of the source frame (rotations scaled to mm at the scene distance)
    only along eigen-directions above ``weak_rel`` of the strongest. Those directions are
    re-estimated every step, so a long run can still wander along weak ones; at the end the total
    motion from ``T0`` is therefore projected onto the strong directions of the fine level and
    polished there (``polish`` steps with that subspace fixed). On a plane, a sphere or a body of
    revolution the unconstrained motions keep the guess, instead of sliding wherever the noise and
    the edges of the overlap push them. ``ref`` (default ``T0``) is the pose those motions keep:
    from a global match's start, the tracked pose, since features say no more about them.
    """
    T0 = np.asarray(T0, dtype=np.float64)
    T = T0.copy()
    R0 = T0 if ref is None else np.asarray(ref, dtype=np.float64)

    def subset(level: _Level) -> tuple[np.ndarray, np.ndarray]:
        step = max(1, -(-len(level.pts) // _ICP_POINTS))  # an even spread of at most _ICP_POINTS points
        return level.pts[::step], level.nrm[::step]

    for coarse, dmax, n_it in levels:
        s, t = (src.coarse, dst.coarse) if coarse else (src.fine, dst.fine)
        P, N = subset(s)
        for _ in range(int(n_it)):
            sysm = _normal_system(P, N, t, T, dmax, robust_min_mm)
            if sysm is None:
                break
            H, g, _, L = sysm
            D, lam, U = _scaled_subspace(H, L, weak_rel)
            xi = -D * (U @ ((U.T @ (D * g)) / lam))
            if not np.all(np.isfinite(xi)):
                break
            T = T @ _exp6(xi)
            if np.linalg.norm(xi[:3]) < 1e-5 and np.linalg.norm(xi[3:]) < 1e-3:
                break
    # keep only the motion along the fine level's strong directions, then polish inside them
    d_fine = float(levels[-1][1])
    sysm = _normal_system(src.fine.pts, src.fine.nrm, dst.fine, T, d_fine, robust_min_mm)
    if sysm is None:
        return T
    H, _, _, L = sysm
    D, _, U = _scaled_subspace(H, L, weak_rel)
    d = _vec6(invert_se3(R0) @ T) / D  # the motion from the guess (or ref), scaled
    T = R0 @ _exp6(D * (U @ (U.T @ d)))
    P, N = subset(src.fine)
    for _ in range(int(polish)):
        sysm = _normal_system(P, N, dst.fine, T, d_fine, robust_min_mm)
        if sysm is None:
            break
        H, g, _, _ = sysm
        Hs = U.T @ (H * D[:, None] * D[None, :]) @ U
        try:
            a = -np.linalg.solve(Hs, U.T @ (D * g))
        except np.linalg.LinAlgError:
            break
        xi = D * (U @ a)
        T = T @ _exp6(xi)
        if np.linalg.norm(xi[:3]) < 1e-5 and np.linalg.norm(xi[3:]) < 1e-3:
            break
    return T


def _features(cloud: _Cloud, p: StraightenParams) -> tuple[Any, Any]:
    """The coarse level as an Open3D cloud and its FPFH features (cached on the cloud)."""
    if cloud.feat is None:
        o3d = _open3d()
        pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(cloud.coarse.pts))
        pc.normals = o3d.utility.Vector3dVector(cloud.coarse.nrm)
        search = o3d.geometry.KDTreeSearchParamHybrid(radius=float(p.fpfh_radius_mm), max_nn=100)
        cloud.feat = (pc, o3d.pipelines.registration.compute_fpfh_feature(pc, search))
    return cloud.feat


GLOBAL_METHODS = ("fgr", "ransac")


def global_match(src: _Cloud, dst: _Cloud, params: StraightenParams | None = None, method: str = "fgr",
                 seed: int = 0) -> np.ndarray | None:
    """A first guess of ``src`` into ``dst`` with no pose to start from, from FPFH features of the
    coarse clouds: ``"fgr"`` (Fast Global Registration, Zhou et al. 2016) or ``"ransac"`` (RANSAC
    on feature matches with edge-length and distance checks, ``seed``ed), both in Open3D. ``None``
    when it fails. Either can return a wrong match (a mirror view, a repeated shape): it is only a
    start for the verification."""
    p = params if params is not None else StraightenParams()
    o3d = _open3d()
    reg = o3d.pipelines.registration
    (ps, fs), (pd, fd) = _features(src, p), _features(dst, p)
    dist = float(p.fgr_max_corr_mm)
    try:
        if method == "fgr":
            res = reg.registration_fgr_based_on_feature_matching(
                ps, pd, fs, fd, reg.FastGlobalRegistrationOption(maximum_correspondence_distance=dist))
        elif method == "ransac":
            if hasattr(o3d.utility, "random"):
                o3d.utility.random.seed(int(seed))
            res = reg.registration_ransac_based_on_feature_matching(
                ps, pd, fs, fd, True, dist, reg.TransformationEstimationPointToPoint(False), 3,
                [reg.CorrespondenceCheckerBasedOnEdgeLength(0.9), reg.CorrespondenceCheckerBasedOnDistance(dist)],
                reg.RANSACConvergenceCriteria(100_000, 0.999))
        else:
            raise ValueError(f"method must be one of {GLOBAL_METHODS}, got {method!r}")
    except RuntimeError:  # pragma: no cover - Open3D refuses degenerate input
        return None
    T = np.asarray(res.transformation, dtype=np.float64)
    return T if np.all(np.isfinite(T)) and res.fitness > 0 else None


def _plausible(src: _Cloud, T_guess: np.ndarray, T: np.ndarray, bound_mm: float, max_deg: float) -> bool:
    """Is ``T`` within the drift bounds (with a margin) of the tracked guess? (Cheap: no matching.)"""
    Dm = invert_se3(np.asarray(T_guess)) @ np.asarray(T)
    P = src.coarse.pts
    moved = float(np.sqrt(np.mean(np.sum((P @ Dm[:3, :3].T + Dm[:3, 3] - P) ** 2, axis=1))))
    return moved <= 1.5 * bound_mm and rotation_angle_deg(Dm[:3, :3]) <= 1.5 * max_deg


def verify_loop(src: _Cloud, dst: _Cloud, T_guess: np.ndarray, params: StraightenParams | None = None,
                bound_mm: float | None = None, a: int = 0, b: int = 0, *, start: np.ndarray | None = None,
                large: bool = False) -> LoopCheck:
    """Point-to-plane ICP of ``src`` onto ``dst`` from ``T_guess`` and the acceptance tests (step 4).

    ``bound_mm`` is the plausible drift between the two keyframes (RMS point movement that ICP may
    apply to the guess); ``None`` uses ``max_drift_mm``. ``large``: a large-drift check, bounded by
    ``global_max_drift_mm`` / ``global_max_drift_deg`` instead, which may ``start`` ICP elsewhere
    (a :func:`global_match`); the unmeasurable directions still keep ``T_guess``.
    """
    p = params if params is not None else StraightenParams()
    cap = p.global_max_drift_mm if large else p.max_drift_mm
    bound = float(cap if bound_mm is None else min(bound_mm, cap))
    max_deg = float(p.global_max_drift_deg if large else p.max_drift_deg)
    out = LoopCheck(a=a, b=b, bound_mm=bound, large=large)
    T_guess = np.asarray(T_guess, dtype=np.float64)
    T0 = T_guess if start is None else np.asarray(start, dtype=np.float64)
    d_fine = float(p.icp_fine_mm)
    reach = 2.0 * p.fgr_max_corr_mm if start is not None else 1.5 * bound
    d_coarse = float(np.clip(reach, 2.5 * p.coarse_voxel_mm, 30.0))
    d_mid = max(2.0 * d_fine, 2.5 * p.voxel_mm)
    it = p.icp_iterations
    T = _icp(src, dst, T0, ((True, d_coarse, it[0]), (False, d_mid, it[1]), (False, d_fine, it[2])),
             p.weak_rel, p.robust_min_mm, ref=T_guess if start is not None else None)
    if not np.all(np.isfinite(T)):
        out.reason = "ICP diverged"
        return out
    out.T_ba = T
    i, j = _match(src.fine.pts, src.fine.nrm, dst.fine, T, d_fine)
    n = len(i)
    out.n_inliers = n
    out.fitness = n / max(len(src.fine.pts), 1)
    Ti = invert_se3(T)
    i2, _ = _match(dst.fine.pts, dst.fine.nrm, src.fine, Ti, d_fine)
    out.overlap = min(out.fitness, len(i2) / max(len(dst.fine.pts), 1))
    if n:
        P = src.fine.pts[i]
        Nt = dst.fine.nrm[j]
        r = np.einsum("ij,ij->i", P @ T[:3, :3].T + T[:3, 3] - dst.fine.pts[j], Nt)
        out.rmse_mm = float(np.sqrt(np.mean(r * r)))
        Ns = Nt @ T[:3, :3]  # target normals in the source frame
        J = np.concatenate([np.cross(P, Ns), Ns], axis=1)  # motion of the source frame (the edge residual)
        L = float(np.median(np.linalg.norm(P, axis=1)))
        out.J2_full = J.T @ J / n
        D, lam_all, _ = _scaled_subspace(out.J2_full, L, 0.0)
        out.conditioning = float(max(lam_all[0], 0.0) / max(lam_all[-1], 1e-30)) if len(lam_all) else 0.0
        D, lam, U = _scaled_subspace(out.J2_full, L, p.weak_rel)
        out.dof = int(len(lam))
        out.J2 = (U * lam) @ U.T / D[:, None] / D[None, :]  # constrained directions only
        Dm = invert_se3(T_guess) @ T  # what ICP changed, as a motion of the source frame
        out.moved_mm = float(np.sqrt(np.mean(np.sum((P @ Dm[:3, :3].T + Dm[:3, 3] - P) ** 2, axis=1))))
        out.moved_deg = rotation_angle_deg(Dm[:3, :3])
        sigma = max(float(p.loop_sigma_mm), 1e-6)
        out.info = out.J2 / sigma**2
        if p.information == "open3d":
            # Open3D's point-to-point version (Redwood), in the same (source) frame: its "target" argument
            # holds the points whose coordinates enter it; scaled to the same translation weight
            reg = _open3d().pipelines.registration
            info = np.asarray(reg.get_information_matrix_from_point_clouds(_as_o3d(dst), _as_o3d(src), d_fine, Ti))
            out.info = info / max(info[5, 5], 1.0) / sigma**2
    if n < p.min_inliers:
        out.reason = f"too few matching points ({n} < {p.min_inliers})"
    elif out.dof < 1:
        out.reason = "the matched surfaces have no shape to measure"
    elif out.overlap < p.min_overlap:
        out.reason = f"too little overlap ({100 * out.overlap:.0f} % < {100 * p.min_overlap:.0f} %)"
    elif out.rmse_mm > p.max_rmse_mm:
        out.reason = f"the surfaces do not match closely ({out.rmse_mm:.2f} mm RMS > {p.max_rmse_mm:.2f} mm)"
    elif out.moved_mm > bound or out.moved_deg > max_deg:
        out.reason = (f"the match is farther from the tracked pose than drift explains ({out.moved_mm:.1f} mm, "
                      f"{out.moved_deg:.1f} deg; at most {bound:.1f} mm, {max_deg:.1f} deg)")
    else:
        out.accepted = True
    return out


def loop_residual_mm(T: np.ndarray, loop: LoopCheck) -> float:
    """RMS offset (mm, along the normals) of a loop's matched points at node poses ``T`` (K,4,4)."""
    e = _vec6(invert_se3(loop.T_ba) @ _rel(T, loop.a, loop.b))
    return float(math.sqrt(max(float(e @ loop.J2 @ e), 0.0)))


# --------------------------------------------------------------------------- 5. pose graph
@dataclass
class OdometryEdge:
    """Pose-graph edge from keyframe ``k`` to ``k + 1``: the tracked relative pose ``X = inv(T[k+1])
    @ T[k]`` and the information (6x6, rotation then translation) of the drift model."""

    k: int
    X: np.ndarray
    info: np.ndarray


def odometry_edges(T_kf: np.ndarray, frames: Sequence[float], lost: Sequence[float] | None = None,
                   params: StraightenParams | None = None) -> list[OdometryEdge]:
    """Odometry edges between consecutive keyframes ``T_kf`` (K,4,4): tracked relative poses with the
    information of a random walk of ``drift_deg_per_frame`` / ``drift_mm_per_frame`` per processed
    frame. ``frames[k]``: processed frames from keyframe ``k`` to ``k + 1``; ``lost[k]``: how many of
    them failed to track (each counts ``LOST_FRAME_WEIGHT`` frames)."""
    p = params if params is not None else StraightenParams()
    lost = np.zeros(len(frames)) if lost is None else lost
    out = []
    sr = math.radians(float(p.drift_deg_per_frame))
    st = float(p.drift_mm_per_frame)
    for k in range(len(T_kf) - 1):
        n = max(1.0, float(frames[k]) + (LOST_FRAME_WEIGHT - 1.0) * float(lost[k]))
        info = np.diag([1.0 / (n * sr * sr)] * 3 + [1.0 / (n * st * st)] * 3)
        out.append(OdometryEdge(k, _rel(T_kf, k, k + 1), info))
    return out


def _odometry_chi2(T: np.ndarray, e: OdometryEdge) -> float:
    r = _vec6(invert_se3(e.X) @ _rel(T, e.k, e.k + 1))
    return float(r @ e.info @ r)


def _run_open3d(T_init: np.ndarray, odometry: list[OdometryEdge], loops: list[LoopCheck], active: list[int],
                prune_mm: float, p: StraightenParams) -> tuple[np.ndarray, list[int]]:
    """One ``global_optimization`` (LM + line process) with loops ``active``; returns poses and survivors."""
    o3d = _open3d()
    reg = o3d.pipelines.registration
    g = reg.PoseGraph()
    for T in T_init:
        g.nodes.append(reg.PoseGraphNode(np.asarray(T, dtype=np.float64)))
    for e in odometry:
        g.edges.append(reg.PoseGraphEdge(e.k, e.k + 1, e.X, e.info, uncertain=False))
    for li in active:
        L = loops[li]
        g.edges.append(reg.PoseGraphEdge(L.a, L.b, L.T_ba, L.info, uncertain=True))
    # Open3D's line-process weight is preference * max_correspondence_distance^2 * mean(info[5, 5]) over
    # all edges; the preference is chosen so that it equals mu = (prune_mm / loop_sigma_mm)^2. A loop's
    # weighted residual is (its RMS normal offset / loop_sigma_mm)^2, so loops still more than prune_mm
    # off get a weight below 1/4 and are pruned
    mean55 = float(np.mean([np.asarray(e.information)[5, 5] for e in g.edges]))
    mu = (float(prune_mm) / float(p.loop_sigma_mm)) ** 2
    option = reg.GlobalOptimizationOption(max_correspondence_distance=1.0, edge_prune_threshold=0.25,
                                          preference_loop_closure=mu / max(mean55, 1e-12), reference_node=0)
    with o3d.utility.VerbosityContextManager(o3d.utility.VerbosityLevel.Error):
        reg.global_optimization(g, reg.GlobalOptimizationLevenbergMarquardt(),
                                reg.GlobalOptimizationConvergenceCriteria(), option)
    T_out = np.stack([np.asarray(nd.pose, dtype=np.float64) for nd in g.nodes])
    # the pruned graph keeps the surviving loop edges; match them by keyframes and (copied) transform,
    # so that two loops between the same keyframes stay apart
    kept: set[int] = set()
    for e in g.edges:
        if not e.uncertain or e.confidence < 0.25:
            continue
        X = np.asarray(e.transformation)
        for li in active:
            L = loops[li]
            if li not in kept and (L.a, L.b) == (e.source_node_id, e.target_node_id) and np.array_equal(L.T_ba, X):
                kept.add(li)
                break
    return T_out, sorted(kept)


def optimize_pose_graph(T_kf: np.ndarray, odometry: list[OdometryEdge], loops: list[LoopCheck],
                        params: StraightenParams | None = None) -> tuple[np.ndarray, list[int]]:
    """Optimise keyframe poses ``T_kf`` (K,4,4) with odometry and loop edges (module docstring, step 5).

    The line-process pruning threshold is graduated from "every loop" down to ``loop_prune_mm``.
    Returns the optimised poses (the input when no loop survives) and the indices of the loops kept.
    """
    p = params if params is not None else StraightenParams()
    T_in = np.asarray(T_kf, dtype=np.float64)
    active = list(range(len(loops)))
    if not active:
        return T_in.copy(), []
    r0 = max(loop_residual_mm(T_in, loops[i]) for i in active)
    d_end = float(p.loop_prune_mm)
    d_start = max(1.5 * r0, d_end)
    n_stages = 1 if d_start <= 1.01 * d_end else 3
    stages = [d_start * (d_end / d_start) ** (s / max(n_stages - 1, 1)) for s in range(n_stages)]
    T_cur = T_in
    for d in stages:
        T_cur, active = _run_open3d(T_cur, odometry, loops, active, d, p)
        if not active:
            return T_in.copy(), []
    # Open3D keeps the reference node up to rounding; make it exact (it defines the world frame)
    T_cur = np.stack([T_in[0] @ invert_se3(T_cur[0]) @ T for T in T_cur])
    T_cur[0] = T_in[0]
    return T_cur, active


# --------------------------------------------------------------------------- 7. corrections
def interpolate_poses(times: np.ndarray, T_in: np.ndarray, kf_times: np.ndarray, T_kf_in: np.ndarray,
                      T_kf_out: np.ndarray) -> np.ndarray:
    """Corrected poses of frames at ``times`` (tracked poses ``T_in``, (M,4,4)) from the keyframes'
    corrections ``T_kf_out @ inv(T_kf_in)``: each frame is moved with both neighbouring keyframes and
    the two results are blended by time (rotation slerp, translation lerp). Frames outside the
    keyframes' time span take the nearest keyframe's correction."""
    times = np.asarray(times, dtype=np.float64)
    kf_times = np.asarray(kf_times, dtype=np.float64)
    # an unchanged keyframe (the reference) gets exactly no correction, whatever the rounding of its pose
    C = np.stack([np.eye(4) if np.array_equal(T_kf_out[k], T_kf_in[k]) else T_kf_out[k] @ invert_se3(T_kf_in[k])
                  for k in range(len(T_kf_in))])
    out = np.empty_like(np.asarray(T_in, dtype=np.float64))
    K = len(kf_times)
    for m, (t, T) in enumerate(zip(times, T_in)):
        j = int(np.searchsorted(kf_times, t, side="right"))
        a, b = max(0, min(j - 1, K - 1)), max(0, min(j, K - 1))
        Pa = C[a] @ T
        if b == a or kf_times[b] <= kf_times[a]:
            out[m] = Pa
            continue
        alpha = float(np.clip((t - kf_times[a]) / (kf_times[b] - kf_times[a]), 0.0, 1.0))
        Pb = C[b] @ T
        R = _slerp(Pa[:3, :3], Pb[:3, :3], alpha)
        out[m] = np.eye(4)
        out[m, :3, :3] = R
        out[m, :3, 3] = (1.0 - alpha) * Pa[:3, 3] + alpha * Pb[:3, 3]
    return out


# --------------------------------------------------------------------------- main entry
class _Steps:
    def __init__(self, progress: Callable[[int, int], None] | None, cancelled: Callable[[], bool] | None) -> None:
        self.progress, self.cancelled = progress, cancelled
        self.k, self.n = 0, 1

    def check(self) -> None:
        if self.cancelled is not None and self.cancelled():
            raise InterruptedError("straightening cancelled")

    def step(self) -> None:
        self.k += 1
        if self.progress is not None:
            self.progress(min(self.k, self.n), self.n)
        self.check()


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _where(index: int, first: int, last: int) -> str:
    if last <= first:
        return "the scan"
    u = (index - first) / (last - first)
    return "the end of the scan" if u > 2 / 3 else "the middle of the scan" if u > 1 / 3 else "the start of the scan"


def _gt_summary(err: dict) -> dict:
    return {k: v for k, v in err.items() if k != "per_frame"}


def _revisit_errors(gt: dict[int, np.ndarray], poses: dict[int, np.ndarray], pairs: Sequence[tuple[int, int]],
                    dist_mm: float, gaps: dict[tuple[int, int], np.ndarray]) -> dict:
    """How well the passes line up at revisits, against the truth.

    For frame pairs ``(i, j)``, the error of the relative pose ``inv(T_j) @ T_i``: ``offset`` is
    how far the point ``dist_mm`` in front of camera ``i`` lands from where it should (mm, any
    direction, including sliding along the surface) and ``rot`` its rotation (deg). For verified
    loops (``gaps``: their untruncated mean ``J^T J``), ``gap`` is the RMS distance between the two
    passes' surfaces along their normals (mm): what shows as doubled surfaces.
    """
    p = np.array([0.0, 0.0, float(dist_mm)])
    off, deg, gap = [], [], []
    for i, j in pairs:
        if i not in gt or j not in gt or i not in poses or j not in poses:
            continue
        D = invert_se3(invert_se3(gt[j]) @ gt[i]) @ (invert_se3(poses[j]) @ poses[i])
        off.append(float(np.linalg.norm(D[:3, :3] @ p + D[:3, 3] - p)))
        deg.append(rotation_angle_deg(D[:3, :3]))
        if (i, j) in gaps:
            e = _vec6(D)
            gap.append(math.sqrt(max(float(e @ gaps[(i, j)] @ e), 0.0)))
    if not off:
        return {"n": 0}

    def stats(v: list[float]) -> dict:
        a = np.asarray(v)
        return {"max": float(a.max()), "rms": float(np.sqrt(np.mean(a * a))), "median": float(np.median(a))}

    out = {"n": len(off), "offset_mm": stats(off), "rot_deg": stats(deg)}
    if gap:
        out["gap_mm"] = {"n": len(gap), **stats(gap)}
    return out


def straighten_trajectory(
    rec_path: str | Path,
    calib: Any,
    trajectory: Sequence[Sequence[Any]] | str | Path,
    *,
    params: StraightenParams | None = None,
    depth_params: Any = None,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[dict[int, np.ndarray], dict]:
    """Close loops in a tracked scan and return corrected poses (see the module docstring).

    ``trajectory``: ``[(frame_index, t_host, ok, T_wc 4x4)]`` as ``process_recording`` writes
    ``trajectory.csv`` (or that file's path). ``calib`` defaults to the recording's
    ``calibration.json``; ``depth_params`` to the pipeline's depth settings at the cloud resolution.
    ``progress(k, n)`` is called after each step with ``k`` of ``n`` steps done (one per keyframe
    cloud and per loop candidate, then the optimisation and the corrections; the last call is
    ``(n, n)``, also when the answer comes early). ``cancelled()`` is polled between steps and
    ``InterruptedError`` raised when it returns true.

    Returns ``(poses, report)``: the corrected ``T_wc`` of every tracked frame (the input poses
    when nothing is changed) and the report described in the module docstring.
    """
    from ohs.app.process import trajectory_errors  # noqa: PLC0415
    from ohs.io.recording import RecordingReader  # noqa: PLC0415

    wall0 = time.perf_counter()
    p = params if params is not None else StraightenParams()
    steps = _Steps(progress, cancelled)
    steps.check()
    rows = _as_rows(trajectory)
    reader = RecordingReader(rec_path)
    if calib is None:
        calib = reader.calibration()
    if calib is None:
        raise ValueError(f"no calibration for {rec_path}: pass one or add calibration.json to the recording")
    size = reader.meta.get("image_size") or (list(reader.frame(0).image_size) if len(reader) else None)
    if size and tuple(int(v) for v in size) != tuple(int(v) for v in calib.stereo.image_size):
        raise ValueError(f"the calibration is for {calib.stereo.image_size[0]}x{calib.stereo.image_size[1]} images "
                         f"but the recording has {size[0]}x{size[1]}: use the calibration of this camera mode")

    tracked = [r for r in rows if r[2]]
    poses_in = {int(i): np.asarray(T, dtype=np.float64).copy() for i, _, _, T in tracked}
    timings: dict[str, float] = {}
    report: dict[str, Any] = {
        "keyframes": 0, "loop_candidates": 0, "loops_accepted": 0, "correction_mm": 0.0, "correction_deg": 0.0,
        "loops": [], "verdict": "", "changed": False, "reason": "",
        "frames": {"rows": len(rows), "tracked": len(tracked)},
        "candidates": [], "residual_mm": None, "timings_s": timings, "compute": None,
        "params": asdict(p),
    }
    gt = reader.ground_truth_poses()
    gaps: dict[tuple[int, int], np.ndarray] = {}  # verified loops (frame pairs): J^T J for the truth's surface gap

    def finish(poses: dict[int, np.ndarray], reason: str, verdict: str) -> tuple[dict[int, np.ndarray], dict]:
        if progress is not None and steps.k < steps.n:  # an early answer skips the remaining steps
            progress(steps.n, steps.n)
        report["reason"], report["verdict"] = reason, verdict
        report["changed"] = any(not np.array_equal(poses[i], poses_in[i]) for i in poses_in)
        if gt:
            est_in = [(i, T, True) for i, T in poses_in.items()]
            est_out = [(i, poses[i], True) for i in poses_in]
            report["ground_truth"] = {"before": _gt_summary(trajectory_errors(gt, est_in)),
                                      "after": _gt_summary(trajectory_errors(gt, est_out))}
            pairs = [tuple(c["frames"]) for c in report["candidates"]]
            if pairs:  # the places where the scan passes over an earlier part
                report["ground_truth"]["revisits"] = {
                    "before": _revisit_errors(gt, poses_in, pairs, p.view_distance_mm, gaps),
                    "after": _revisit_errors(gt, poses, pairs, p.view_distance_mm, gaps)}
        timings["total"] = time.perf_counter() - wall0
        log.info("straighten %s: %s", rec_path, verdict)
        return poses, report

    if len(tracked) < 2:
        return finish(dict(poses_in), "too_short", "Nothing to straighten: fewer than two frames were tracked.")

    # 1. keyframes
    t0 = time.perf_counter()
    kf_pos = select_keyframes(rows, p.kf_travel_mm, p.kf_rotation_deg, p.kf_max_gap_s, p.max_keyframes)
    T_kf = np.stack([rows[k][3] for k in kf_pos])
    kf_times = np.array([rows[k][1] for k in kf_pos])
    kf_frames = [rows[k][0] for k in kf_pos]
    travel = _cumulative_travel(rows)[kf_pos]
    report["keyframes"] = len(kf_pos)
    report["keyframe_frames"] = [int(i) for i in kf_frames]
    # 2. loop candidates
    cands = find_loop_candidates(T_kf, travel, p) if len(kf_pos) >= 2 else []
    report["loop_candidates"] = len(cands)
    timings["keyframes"] = time.perf_counter() - t0
    if not cands:
        return finish(dict(poses_in), "no_candidates",
                      "No loops found: to let Studio straighten a scan, pass over an earlier part again.")

    # 3-4. clouds and verification
    clouds = KeyframeClouds(reader, calib, p, depth_params)
    report["compute"] = {"depth": clouds.backend, "device": clouds.device,
                         "cloud_size": list(clouds.rectifier.image_size)}
    pos_of = {int(i): k for k, i in enumerate(reader.indices)}
    need = sorted({k for ab in cands for k in ab})
    steps.n = len(need) + len(cands) + 2
    t0 = time.perf_counter()
    kf_cloud: dict[int, _Cloud | None] = {}
    missing: set[int] = set()
    for k in need:
        pos = pos_of.get(int(kf_frames[k]))
        if pos is None:
            missing.add(k)
        kf_cloud[k] = clouds.get(pos) if pos is not None else None
        steps.step()
    timings["clouds"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    checks: list[LoopCheck] = []
    for a, b in cands:
        ca, cb = kf_cloud.get(a), kf_cloud.get(b)
        if ca is None or cb is None:
            chk = LoopCheck(a=a, b=b, reason="a frame is missing from the recording" if {a, b} & missing
                            else "too little depth in one of the two frames")
        else:
            bound = float(p.drift_slack_mm + p.drift_frac * (travel[b] - travel[a]))
            guess = _rel(T_kf, a, b)
            chk = verify_loop(ca, cb, guess, p, bound, a, b)
            if not chk.accepted and p.global_registration:
                # large drift: a wider bound from the tracked pose, else a global first guess
                big = float(p.drift_slack_mm + p.global_drift_frac * (travel[b] - travel[a]))
                alt = verify_loop(ca, cb, guess, p, big, a, b, large=True)
                for method in GLOBAL_METHODS if not alt.accepted else ():
                    start = global_match(ca, cb, p, method)
                    if start is None or not _plausible(ca, guess, start, min(big, p.global_max_drift_mm),
                                                       p.global_max_drift_deg):
                        continue
                    alt2 = verify_loop(ca, cb, guess, p, big, a, b, start=start, large=True)
                    alt2.via = "global"
                    if alt2.accepted:
                        alt = alt2
                        break
                if alt.accepted:
                    chk = alt
        checks.append(chk)
        steps.step()
    timings["icp"] = time.perf_counter() - t0
    timings.update({f"clouds_{k}": v / 1e3 for k, v in clouds.timings_ms.items()})
    del kf_cloud, clouds  # the clouds and the depth matcher's (GPU) buffers are not needed any more
    if sum(c.accepted for c in checks) < p.global_min_loops:  # a lone large correction can't bend the scan alone
        for c in checks:
            if c.accepted and c.large:
                c.accepted = False
                c.reason = "a correction this large needs a second loop to agree"
    for c in checks:
        report["candidates"].append({"frames": [int(kf_frames[c.a]), int(kf_frames[c.b])], **c.summary()})
        if c.accepted:
            gaps[(int(kf_frames[c.a]), int(kf_frames[c.b]))] = c.J2_full
    loops = [c for c in checks if c.accepted]
    if not loops:
        return finish(dict(poses_in), "not_verified",
                      f"Found {_plural(len(cands), 'place')} where the scan passes over an earlier part, but none "
                      "lined up reliably (too little overlap or shape to match), so the scan is unchanged.")

    # 5. pose graph (with the never-worse retries of step 6)
    t0 = time.perf_counter()
    frames = np.diff(np.asarray(kf_pos))
    lost = np.array([sum(1 for r in rows[kf_pos[k]:kf_pos[k + 1]] if not r[2]) for k in range(len(kf_pos) - 1)])
    # the drift model, scaled up when the loops show more drift than it expects (median over loops of
    # how far ICP moved them against the random walk's spread over their frames)
    scale = 1.0
    if p.adaptive_drift:
        spread = math.hypot(p.drift_mm_per_frame, p.view_distance_mm * math.radians(p.drift_deg_per_frame))
        ratios = [c.moved_mm / max(spread * math.sqrt(max(kf_pos[c.b] - kf_pos[c.a], 1)), 1e-9) for c in loops]
        scale = float(np.clip(np.median(ratios), 1.0, p.max_drift_scale))
    report["drift_scale"] = round(scale, 2)
    pg_params = p if scale <= 1.0 else replace(p, drift_mm_per_frame=p.drift_mm_per_frame * scale,
                                                drift_deg_per_frame=p.drift_deg_per_frame * scale)
    odometry = odometry_edges(T_kf, frames, lost, pg_params)
    use = list(range(len(loops)))
    T_opt, kept = T_kf, []
    reason, why = "", ""
    for attempt in range(int(p.max_retries) + 1):
        sub = [loops[i] for i in use]
        T_opt, kept_sub = optimize_pose_graph(T_kf, odometry, sub, p)
        kept = [use[i] for i in kept_sub]
        if not kept:
            reason = "disagree"
            break
        before = np.array([loop_residual_mm(T_kf, loops[i]) for i in kept])
        after = np.array([loop_residual_mm(T_opt, loops[i]) for i in kept])
        chi2 = np.array([_odometry_chi2(T_opt, e) for e in odometry])
        report["residual_mm"] = {"before_max": float(before.max()), "after_max": float(after.max()),
                                 "before_rms": float(np.sqrt(np.mean(before**2))),
                                 "after_rms": float(np.sqrt(np.mean(after**2))),
                                 "odometry_chi2_max": float(chi2.max()) if len(chi2) else 0.0}
        if before.max() < p.min_residual_mm:
            reason = "already_straight"
            break
        if not np.sum(after**2) <= p.min_improvement * np.sum(before**2):
            reason, why = "not_better", "closing them would not make the passes line up clearly better"
        elif len(chi2) and chi2.max() > p.max_odometry_chi2:
            reason, why = "bends_too_much", "closing them would bend the scan more than tracking drift can explain"
        else:
            reason = "straightened"
            break
        if len(kept) < 2 or attempt == p.max_retries:
            break
        worst = kept[int(np.argmax(after))]
        log.info("straighten: dropping loop %s (%s) and solving again", (loops[worst].a, loops[worst].b), why)
        use = [i for i in kept if i != worst]
    timings["optimisation"] = time.perf_counter() - t0
    steps.step()
    report["loops_accepted"] = len(kept) if reason in ("straightened", "already_straight") else 0
    report["loops"] = [[int(kf_frames[loops[i].a]), int(kf_frames[loops[i].b])] for i in kept] \
        if reason in ("straightened", "already_straight") else []
    for i, c in enumerate(checks):  # mark the loops the graph kept
        report["candidates"][i]["kept"] = bool(c.accepted and any(loops[j] is c for j in kept)
                                               and reason in ("straightened", "already_straight"))

    n_loops = len(kept)
    if reason == "disagree":
        return finish(dict(poses_in), reason,
                      f"Found {_plural(len(loops), 'loop')}, but they disagreed with the rest of the scan, so the scan "
                      "is unchanged.")
    if reason == "already_straight":
        return finish(dict(poses_in), reason,
                      f"Closed {_plural(n_loops, 'loop')}: the passes already line up (within "
                      f"{report['residual_mm']['before_max']:.2f} mm), so the scan is unchanged.")
    if reason != "straightened":
        return finish(dict(poses_in), reason,
                      f"Found {_plural(len(loops), 'loop')}, but {why}, so the scan is unchanged.")

    # 7. corrections for every tracked frame
    t0 = time.perf_counter()
    idx = [int(i) for i, _, _, _ in tracked]
    T_track = np.stack([np.asarray(T, dtype=np.float64) for _, _, _, T in tracked])
    times = np.array([float(t) for _, t, _, _ in tracked])
    T_new = interpolate_poses(times, T_track, kf_times, T_kf, T_opt)
    poses = {i: T_new[m] for m, i in enumerate(idx)}
    moved = np.linalg.norm(T_new[:, :3, 3] - T_track[:, :3, 3], axis=1)
    turned = np.array([rotation_angle_deg(T_track[m, :3, :3].T @ T_new[m, :3, :3]) for m in range(len(idx))])
    m_max = int(np.argmax(moved))
    report["correction_mm"] = float(moved.max())
    report["correction_deg"] = float(turned.max())
    timings["interpolation"] = time.perf_counter() - t0
    steps.step()
    where = _where(idx[m_max], idx[0], idx[-1])
    return finish(poses, "straightened",
                  f"Closed {_plural(n_loops, 'loop')}: {where} moved {moved.max():.1f} mm into place"
                  + (f" (and turned {turned.max():.2f}°)." if turned.max() >= 0.05 else "."))
