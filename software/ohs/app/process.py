"""Offline processing of a recording at full quality (``ohs process <recording>``).

:func:`process_recording` runs :class:`ohs.app.pipeline.FramePipeline` (default: markerless, full
resolution, 1 mm voxels) over the raw frames of a recording folder and writes into ``out_dir``:

* ``mesh.ply`` (and ``mesh.stl`` / ``mesh.obj`` per ``ProcessParams.export``): marching cubes
  of the TSDF (or screened Poisson with ``poisson=True``), small disconnected pieces removed
  (``clean_min_component`` of the faces), world frame, mm;
* ``laser_points.ply``: the laser cloud, voxel-averaged at ``laser_voxel_mm`` (0.2 mm);
* ``trajectory.csv``: ``index,t,ok,T00..T33`` (``T_wc`` row-major) per processed frame;
* ``marker_map.json`` (marker mode): the refined tag map (world corners, mm);
* ``report.json``: frame counts, tracked fraction, failure reasons, per-stage timings, mesh /
  laser statistics, effective parameters and, when the recording has ``ground_truth/poses.csv``
  (simulated data), the trajectory error against it.

The calibration defaults to the recording's ``calibration.json``. IMU samples are fed to the
pipeline incrementally (up to 0.25 s ahead of each frame), so recordings longer than the gyro
buffer work. **Cuts:** frames whose ``t_host`` lies inside any ``events.csv`` row with
``kind == "cut"`` and ``value == "t0,t1"`` (inclusive; written by the app's live rewind) are
skipped; ``report["frames"]["cut"]`` counts them and ``report["cuts"]`` lists the intervals.
Malformed cut values are ignored with a warning.

**Frame selection.** The candidates are the frames ``range(start, stop, frame_step)`` outside the
cuts. By default every candidate is processed. With ``frame_motion_deg > 0`` (maintenance plan
F2 step 2) frames are picked by the gyro (:func:`pick_frames_by_motion`): the first candidate,
the first one after each cut, and then the next candidate at which the gyro's rotation since the
last processed frame reaches ``frame_motion_deg`` or the gap reaches ``frame_max_gap`` recorded
frames, whichever comes first (``frame_step`` stays the smallest gap). Where the IMU samples do
not cover a stretch the candidates there are all processed. Without IMU samples, without an IMU
calibration or with ``use_imu=False`` it falls back to every candidate, with a warning, and
``report["frames"]["selection"]`` says so. A gyro sees rotation only: a hand that slides without
turning is caught by the cap alone. With ``poses`` no new pick is made: the frames that have a pose
are fused (the second pass then fuses exactly the frames of a motion-picked first pass, as long
as ``frame_step``, ``start`` and ``stop`` are the same).
``report["frames"]["selection"]``: ``{"by": "step"}``, or with motion picking the threshold, the
cap, how many frames were picked by rotation / by the cap / first (start and after cuts) /
without gyro, the mean gap and a histogram of the gaps (recorded frames; not across cuts).

Marker mode calls ``MarkerTracker.refine_map()`` after the last frame and saves the
refined map; the fused model and laser cloud use the poses tracked online (refinement mostly
improves the map itself; see VALIDATION.md).

Ground-truth trajectory errors are computed after aligning the estimated world frame with the
ground truth at the first tracked frame: ``A = T_gt[f] @ inv(T_est[f])`` and per frame
``pose_error(inv(A) @ T_gt[i], T_est[i])``. In markerless mode ``T_est[f] = I``, so this is
``inv(T_gt0) @ T_gt_i`` versus ``T_est_i``.

**Known poses** (``poses={frame index: T_wc}``, the processing studio's straighten and fuse
stages): no tracking at all. Only the frames that have a pose (and pass the frame selection and
the cuts) are processed, each fused at its pose with :meth:`FramePipeline.process` ``(pose=T)``;
the pipeline runs with a single model and no gyro (nothing is tracked), and the marker map is
not refined. ``trajectory.csv`` then holds the given poses, the report says
``"poses": "given"``, and the ground-truth errors (simulated data) measure those poses.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import time
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ohs.app.pipeline import FrameResult, FramePipeline, PipelineParams
from ohs.geometry import integrate_gyro, invert_se3, pose_error
from ohs.io.recording import RecordingReader
from ohs.mesh.confidence import SIGMA_NONE_MM, sigma_stats
from ohs.types import ScannerCalibration

__all__ = ["ProcessParams", "process_recording", "trajectory_errors", "cut_intervals", "pick_frames_by_motion",
           "EXPORT_FORMATS"]

log = logging.getLogger(__name__)

EXPORT_FORMATS = ("ply", "stl", "obj")
IMU_LOOKAHEAD_S = 0.25


@dataclass
class ProcessParams:
    """Offline processing settings (the pipeline settings plus frame selection and export)."""

    pipeline: PipelineParams = field(default_factory=PipelineParams)
    frame_step: int = 1
    start: int = 0
    stop: int | None = None
    export: tuple[str, ...] = ("ply", "stl")
    poisson: bool = False
    clean_min_component: float = 0.01
    laser_voxel_mm: float = 0.2
    poisson_depth: int = 9
    # pick frames by motion (module docstring, "Frame selection"): 0 = off, every frame_step-th frame
    frame_motion_deg: float = 0.0
    frame_max_gap: int = 4


# --------------------------------------------------------------------------- frame selection
MOTION_IMU_MAX_GAP_S = 0.05  # like GyroPredictor.max_gap_s: a longer hole in the IMU stream is no gyro
MOTION_IMU_TOL_S = 0.003  # like GyroPredictor.covers: the interval may stick out of the samples by this much


def _rotation_angle(R: np.ndarray) -> float:
    """Angle (rad) of a rotation matrix, accurate for small angles too."""
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    return float(np.arctan2(0.5 * np.linalg.norm(v), 0.5 * (np.trace(R) - 1.0)))


def pick_frames_by_motion(
    positions: Any,
    frame_t: Any,
    imu_t: Any,
    gyro: Any,
    *,
    threshold_deg: float,
    max_gap: int,
    time_offset: float = 0.0,
    gyro_bias: Any = None,
    restart: Any = (),
) -> tuple[list[int], list[str]]:
    """Choose frames by the gyro's rotation: ``(chosen positions, reason per chosen position)``.

    ``positions`` (ascending recorded-frame positions: the candidates) with their camera times
    ``frame_t`` (s); ``imu_t`` (s, IMU host clock, camera time = ``imu_t + time_offset``) and
    ``gyro`` (N,3) rad/s in the IMU frame as recorded (raw: ``gyro_bias``, IMU frame, is subtracted
    when given). The rotation angle does not depend on the frame, so ``R_ci`` is not needed.

    The first candidate is chosen ("first"). After that a candidate is chosen when, counting from
    the last chosen one, the gyro's integrated rotation (net angle of the rotation, trapezoidal
    integration as in :func:`ohs.geometry.integrate_gyro`) reaches ``threshold_deg``
    ("rotation"), or the gap reaches ``max_gap`` recorded frames ("cap"). A candidate in
    ``restart`` (the first one after a cut) is always chosen and the angle restarts there
    ("restart"). Where the IMU samples do not cover the interval since the last chosen frame
    (no samples, or a hole longer than ``MOTION_IMU_MAX_GAP_S``) the candidate is chosen
    ("no_gyro"): without a gyro nothing is skipped.
    """
    pos = [int(x) for x in positions]
    ft = np.asarray(frame_t, dtype=np.float64).reshape(-1)
    if len(ft) != len(pos):
        raise ValueError(f"{len(pos)} positions but {len(ft)} frame times")
    if threshold_deg <= 0 or int(max_gap) < 1:
        raise ValueError("threshold_deg must be > 0 and max_gap >= 1")
    t = np.asarray(imu_t, dtype=np.float64).reshape(-1) + float(time_offset)
    w = np.asarray(gyro, dtype=np.float64).reshape(-1, 3)
    if len(t) != len(w):
        raise ValueError(f"{len(t)} IMU times but {len(w)} gyro rows")
    if gyro_bias is not None:
        w = w - np.asarray(gyro_bias, dtype=np.float64).reshape(1, 3)
    ok = np.isfinite(t) & np.all(np.isfinite(w), axis=1)
    t, w = t[ok], w[ok]
    order = np.argsort(t, kind="stable")
    t, w = t[order], w[order]
    keep = np.concatenate([[True], np.diff(t) > 0]) if len(t) else np.zeros(0, dtype=bool)
    t, w = t[keep], w[keep]
    thr = math.radians(float(threshold_deg))
    restart = {int(x) for x in restart}

    def covers(lo: float, hi: float) -> bool:
        if len(t) < 2 or t[0] > lo + MOTION_IMU_TOL_S or t[-1] < hi - MOTION_IMU_TOL_S:
            return False
        i0 = max(int(np.searchsorted(t, lo, side="right")) - 1, 0)
        i1 = min(int(np.searchsorted(t, hi, side="left")), len(t) - 1)
        return not (i1 > i0 and float(np.max(np.diff(t[i0:i1 + 1]))) > MOTION_IMU_MAX_GAP_S)

    def rotation(t0: float, t1: float) -> np.ndarray:
        i0 = max(int(np.searchsorted(t, t0, side="right")) - 1, 0)
        i1 = min(int(np.searchsorted(t, t1, side="left")) + 1, len(t))
        return integrate_gyro(t[i0:i1], w[i0:i1], float(t0), float(t1))

    chosen: list[int] = []
    reasons: list[str] = []
    last = R = t_prev = None
    for p_k, t_k in zip(pos, ft):
        why = None
        if last is None:
            why = "first"
        elif p_k in restart:
            why = "restart"
        elif not covers(float(t_prev), float(t_k)):
            why = "no_gyro"
        else:
            R = R @ rotation(float(t_prev), float(t_k))  # chained over the candidates since the last chosen frame
            if _rotation_angle(R) >= thr:
                why = "rotation"
            elif p_k - last >= int(max_gap):
                why = "cap"
        t_prev = t_k
        if why is None:
            continue
        chosen.append(p_k)
        reasons.append(why)
        last, R = p_k, np.eye(3)
    return chosen, reasons


def _selection_summary(chosen: list[int], reasons: list[str], threshold_deg: float, max_gap: int) -> dict:
    """``report["frames"]["selection"]`` of a motion pick: counts per reason and the gaps (recorded frames)."""
    gaps = [b - a for a, b, why in zip(chosen, chosen[1:], reasons[1:]) if why != "restart"]
    c = Counter(reasons)
    return {
        "by": "motion", "threshold_deg": float(threshold_deg), "max_gap": int(max_gap),
        "picked_by_rotation": c["rotation"], "picked_by_cap": c["cap"],
        "picked_first": c["first"] + c["restart"], "picked_without_gyro": c["no_gyro"],
        "mean_gap": float(np.mean(gaps)) if gaps else None,
        "gaps": {str(g): n for g, n in sorted(Counter(gaps).items())},
    }


# --------------------------------------------------------------------------- helpers
def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return _jsonable(x.tolist())
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        v = float(x)
        return v if math.isfinite(v) else None
    if isinstance(x, Path):
        return str(x)
    return x


def _stats(values: list[float]) -> dict[str, float]:
    a = np.asarray(values, dtype=np.float64)
    if a.size == 0:
        return {"n": 0}
    return {"n": int(a.size), "mean": float(a.mean()), "median": float(np.median(a)),
            "p95": float(np.percentile(a, 95)), "max": float(a.max())}


def trajectory_errors(gt: dict[int, np.ndarray], est: list[tuple[int, np.ndarray, bool]]) -> dict:
    """Pose errors of an estimated trajectory against ground truth (see the module docstring).

    ``gt``: frame index -> true ``T_wc``; ``est``: ``(index, T_wc, ok)`` per processed frame.
    Only tracked frames with ground truth are compared. Returns per-frame errors and summaries
    (rotation in degrees, translation in mm); ``{"n": 0}`` if nothing can be compared.
    """
    pairs = [(i, T, gt[i]) for i, T, ok in est if ok and i in gt]
    if not pairs:
        return {"n": 0}
    i0, T_est0, T_gt0 = pairs[0]
    A = T_gt0 @ invert_se3(T_est0)  # estimated world -> ground-truth world
    A_inv = invert_se3(A)
    rows = []
    for i, T_est, T_gt in pairs:
        rot, trans = pose_error(A_inv @ T_gt, T_est)
        rows.append({"index": int(i), "rot_deg": rot, "trans_mm": trans})
    rot = [r["rot_deg"] for r in rows]
    trans = [r["trans_mm"] for r in rows]
    return {
        "n": len(rows),
        "aligned_at_index": int(i0),
        "rot_deg": _stats(rot),
        "trans_mm": _stats(trans),
        "final_rot_deg": rot[-1],
        "final_trans_mm": trans[-1],
        "rms_trans_mm": float(np.sqrt(np.mean(np.square(trans)))),
        "per_frame": rows,
    }


def cut_intervals(reader: RecordingReader) -> list[tuple[float, float]]:
    """``[(t0, t1), ...]`` of the recording's ``cut`` events (``value`` = "t0,t1", host seconds)."""
    out: list[tuple[float, float]] = []
    for e in reader.events():
        if e.kind != "cut":
            continue
        try:
            a, b = (float(x) for x in str(e.value).split(","))
        except ValueError:
            log.warning("ignoring a malformed cut event %r (expected \"t0,t1\")", e.value)
            continue
        if not (math.isfinite(a) and math.isfinite(b)):
            log.warning("ignoring a malformed cut event %r", e.value)
            continue
        out.append((min(a, b), max(a, b)))
    return out


def _check_export(export: tuple[str, ...]) -> tuple[str, ...]:
    fmts = tuple(str(e).lower().lstrip(".") for e in export)
    bad = [e for e in fmts if e not in EXPORT_FORMATS]
    if bad:
        raise ValueError(f"unsupported export format(s) {bad}; choose from {EXPORT_FORMATS}")
    return fmts


def _write_trajectory(path: Path, rows: list[tuple[int, float, bool, np.ndarray]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "t", "ok"] + [f"T{r}{c}" for r in range(4) for c in range(4)])
        for i, t, ok, T in rows:
            w.writerow([int(i), f"{t:.6f}", int(bool(ok))] + [f"{x:.9g}" for x in np.asarray(T).reshape(16)])


def _frame_row(res: FrameResult) -> dict:
    info = res.track.info
    row: dict[str, Any] = {"index": res.index, "t": res.t, "ok": bool(res.track.ok),
                           "total_ms": res.timings_ms.get("total")}
    if not res.track.ok:
        row["reason"] = info.get("reason", "unknown")
    for k in ("fitness", "rmse_mm", "n_inliers", "degenerate", "gyro_prior", "method", "n_tags", "n_stereo",
              "reproj_px", "rms_mm"):
        if k in info:
            row[k] = info[k]
    weak = sorted({str(w.get("kind")) for w in info.get("weak") or () if isinstance(w, dict)})
    if weak:
        row["weak"] = weak  # what the surface couldn't pin down in this frame: "slide", "turn"
    if info.get("input") == "alt":
        row["raw_depth"] = True  # tracked with the raw depth where the filter dropped it (a detailed part)
    if res.laser_points:
        row["n_laser"] = {k: int(len(v)) for k, v in res.laser_points.items()}
    return row


def _weak_summary(rows: list[dict]) -> dict:
    """Fraction of the tracked frames whose surface let the scanner slide, or turn (``info["weak"]``)."""
    ok = [r for r in rows if r.get("ok")]
    return {k: round(sum(k in r.get("weak", ()) for r in ok) / max(len(ok), 1), 3) for k in ("slide", "turn")}


# --------------------------------------------------------------------------- main entry
def process_recording(
    rec_path: str | Path,
    out_dir: str | Path,
    calib: ScannerCalibration | None = None,
    params: ProcessParams | None = None,
    progress: Callable[[int, int, FrameResult], None] | None = None,
    poses: dict[int, np.ndarray] | None = None,
) -> dict:
    """Process a recording at full quality and write the results to ``out_dir``.

    ``calib`` defaults to the recording's ``calibration.json``; ``ValueError`` if neither is
    available or the calibration's image size does not match the recording. ``progress(k, n,
    result)`` is called after each of the ``n`` processed frames (``k`` counts from 0). With
    ``poses`` (frame index -> 4x4 ``T_wc``) nothing is tracked: the frames with a pose are fused
    at it (see the module docstring); ``ValueError`` if no selected frame has one. Returns the
    report dict (also written as ``report.json``).
    """
    wall0 = time.perf_counter()
    rec_path = Path(rec_path)
    out = Path(out_dir)
    p = params if params is not None else ProcessParams()
    if poses is not None:
        poses = {int(k): np.asarray(T, dtype=np.float64).reshape(4, 4) for k, T in poses.items()}
        # nothing is tracked: no tracking model and no gyro prior, only the reconstruction
        p = replace(p, pipeline=replace(p.pipeline, tracking=False, use_imu=False))
    export = _check_export(tuple(p.export))
    if int(p.frame_step) < 1:
        raise ValueError("frame_step must be >= 1")
    motion_deg = float(p.frame_motion_deg)
    if not (math.isfinite(motion_deg) and motion_deg >= 0) or int(p.frame_max_gap) < 1:
        raise ValueError("frame_motion_deg must be >= 0 and frame_max_gap >= 1")
    reader = RecordingReader(rec_path)
    calib_source = "argument"
    if calib is None:
        calib = reader.calibration()
        calib_source = "recording"
    if calib is None:
        raise ValueError(
            f"no calibration for {rec_path}: pass one (e.g. --calib calibration.json) or add calibration.json "
            "to the recording (run the stereo / laser / IMU calibration first)"
        )
    if len(reader) == 0:
        raise ValueError(f"recording {rec_path} has no frames")
    size = reader.meta.get("image_size")
    if not size:
        size = list(reader.frame(0).image_size)
    if tuple(int(v) for v in size) != tuple(int(v) for v in calib.stereo.image_size):
        raise ValueError(
            f"the calibration is for {calib.stereo.image_size[0]}x{calib.stereo.image_size[1]} images but the recording "
            f"has {size[0]}x{size[1]}: use the calibration of this camera mode"
        )
    out.mkdir(parents=True, exist_ok=True)

    pipe = FramePipeline(calib, p.pipeline)
    imu = reader.imu_arrays()
    order = np.argsort(imu["t_host"], kind="stable")
    imu_t, imu_g = imu["t_host"][order], imu["gyro"][order]
    offset = float(calib.imu.time_offset) if calib.imu is not None else 0.0
    j_imu = 0

    stop = len(reader) if p.stop is None else min(int(p.stop), len(reader))
    positions = list(range(max(0, int(p.start)), stop, int(p.frame_step)))
    cuts = cut_intervals(reader)
    n_cut = 0
    if cuts and positions:
        t_pos = reader.times()[np.asarray(positions, dtype=np.int64)]
        inside = np.zeros(len(positions), dtype=bool)
        for a, b in cuts:
            inside |= (t_pos >= a) & (t_pos <= b)
        n_cut = int(inside.sum())
        # the first frame after a cut starts a new stretch (motion selection restarts its angle there)
        after_cut = {pos for k, pos in enumerate(positions) if not inside[k] and k > 0 and inside[k - 1]}
        positions = [pos for pos, cut in zip(positions, inside) if not cut]
        log.info("skipping %d frames inside %d cut interval(s)", n_cut, len(cuts))
        if not positions:
            raise ValueError(f"every selected frame of {rec_path} lies inside a cut (rewound) interval: nothing to process")
    else:
        after_cut = set()
    selection: dict[str, Any] = {"by": "step"}
    if motion_deg > 0 and poses is not None:
        # the second pass fuses exactly the frames of the first (the ones with a pose): no new pick
        selection = {"by": "given poses", "threshold_deg": motion_deg, "max_gap": int(p.frame_max_gap)}
    elif motion_deg > 0:
        why_not = (None if len(imu_t) >= 2 and calib.imu is not None and p.pipeline.use_imu else
                   "the recording has no IMU samples" if len(imu_t) < 2 else
                   "there is no IMU calibration" if calib.imu is None else "the IMU is switched off (use_imu=False)")
        if why_not is not None:
            log.warning("cannot pick frames by motion: %s; processing every %d. frame instead", why_not,
                        int(p.frame_step))
            selection = {"by": "step", "threshold_deg": motion_deg, "max_gap": int(p.frame_max_gap),
                         "fallback": why_not}
        else:
            n_cand = len(positions)
            t_cand = reader.times()[np.asarray(positions, dtype=np.int64)]
            positions, why = pick_frames_by_motion(
                positions, t_cand, imu_t, imu_g, threshold_deg=motion_deg, max_gap=int(p.frame_max_gap),
                time_offset=offset, gyro_bias=calib.imu.gyro_bias, restart=after_cut)
            selection = _selection_summary(positions, why, motion_deg, int(p.frame_max_gap))
            log.info("picked %d of %d frames by motion (%.2f deg or a gap of %d): %d by rotation, %d by the cap",
                     len(positions), n_cand, motion_deg, int(p.frame_max_gap), selection["picked_by_rotation"],
                     selection["picked_by_cap"])
            if selection["picked_without_gyro"]:
                log.warning("%d frames were picked because the IMU samples do not cover them (no gyro there)",
                            selection["picked_without_gyro"])
    n_unposed = 0
    if poses is not None:
        index_of = reader.indices
        kept = [pos for pos in positions if int(index_of[pos]) in poses]
        n_unposed = len(positions) - len(kept)
        positions = kept
        if not positions:
            raise ValueError(f"none of the given poses belongs to a selected frame of {rec_path}: nothing to fuse")
        log.info("fusing %d frames at given poses (%d selected frames have none)", len(positions), n_unposed)
    n = len(positions)
    log.info("processing %s: %d of %d frames (%s mode, scale %.2f)", rec_path, n, len(reader), p.pipeline.mode, p.pipeline.scale)
    rows: list[dict] = []
    traj_rows: list[tuple[int, float, bool, np.ndarray]] = []
    timings: dict[str, list[float]] = {}
    reasons: Counter = Counter()
    n_prior = 0
    try:
        for k, pos in enumerate(positions):
            frame = reader.frame(pos)
            if pipe.gyro_enabled and j_imu < len(imu_t):
                j_new = int(np.searchsorted(imu_t, frame.t_host - offset + IMU_LOOKAHEAD_S, side="right"))
                if j_new > j_imu:
                    pipe.add_imu_arrays(imu_t[j_imu:j_new], imu_g[j_imu:j_new])
                    j_imu = j_new
            res = pipe.process(frame) if poses is None else pipe.process(frame, pose=poses[int(frame.index)])
            for key, v in res.timings_ms.items():
                timings.setdefault(key, []).append(float(v))
            if not res.track.ok:
                reasons[str(res.track.info.get("reason", "unknown"))] += 1
            n_prior += int(bool(res.track.info.get("gyro_prior", False)))
            rows.append(_frame_row(res))
            traj_rows.append((res.index, res.t, bool(res.track.ok), res.track.T_wc))
            if progress is not None:
                progress(k, n, res)
            if (k + 1) % 25 == 0:
                log.info("  %d / %d frames, %d tracked", k + 1, n, pipe.n_tracked)

        report: dict[str, Any] = {
            "recording": str(rec_path),
            "out_dir": str(out),
            "source": reader.meta.get("source"),
            "scene": reader.meta.get("scene"),
            "calibration": calib_source,
            "mode": p.pipeline.mode,
            "scale": float(p.pipeline.scale),
            "image_size": list(pipe.image_size),
            "frames": {
                "in_recording": len(reader), "processed": n, "tracked": pipe.n_tracked,
                "tracked_fraction": pipe.n_tracked / n if n else 0.0,
                "start": int(p.start), "stop": stop, "step": int(p.frame_step), "cut": n_cut, "selection": selection,
                "weak": _weak_summary(rows),
                "with_raw_depth": round(sum(bool(r.get("raw_depth")) for r in rows if r.get("ok"))
                                        / max(sum(bool(r.get("ok")) for r in rows), 1), 3),
            },
            "cuts": [[a, b] for a, b in cuts],
            "failures": dict(reasons),
            "imu": {"samples": int(len(imu_t)), "gyro_prior": pipe.gyro_enabled, "frames_with_prior": n_prior},
            "timings_ms": {key: _stats(v) for key, v in timings.items()},
        }
        if poses is not None:
            report["poses"] = "given"
            report["frames"]["without_pose"] = n_unposed

        # marker map refinement (offline only; nothing was tracked when the poses were given)
        if pipe.markers is not None and poses is None:
            mt = pipe.markers
            ref = mt.refine_map() if mt.initialized else {"rms_mm": None, "n_tags": 0}
            tag_map = {int(i): X.tolist() for i, X in sorted(mt.map.items())}
            (out / "marker_map.json").write_text(json.dumps(_jsonable({
                "dictionary": pipe.marker_params.dictionary, "tag_mm": pipe.marker_params.tag_mm,
                "frame": "world = camera frame C of the map-initialising frame, mm",
                "corners": tag_map, "n_observations": mt.n_observations,
            }), indent=1), encoding="utf-8")
            report["markers"] = {"map_size": len(tag_map), "refine": ref}

        # mesh
        t0 = time.perf_counter()
        method = "tsdf"
        V, F, N, S = pipe.mesh_with_sigma()  # S: each vertex's standard deviation (mm), confidence TSDF only
        if p.poisson:
            from ohs.track.export import poisson_mesh  # noqa: PLC0415 (open3d)

            P, Np = pipe.points()
            if len(P) >= 10:
                V, F, N = poisson_mesh(P, Np, depth=int(p.poisson_depth))
                method = "poisson"
                S = None  # Poisson makes new vertices: no confidence for them
            else:
                log.warning("too few TSDF points for Poisson meshing; keeping the marching-cubes mesh")
        n_faces_raw = int(len(F))
        if p.clean_min_component > 0 and len(F):
            from ohs.track.export import clean_mesh  # noqa: PLC0415

            V, F, idx = clean_mesh(V, F, min_component_fraction=float(p.clean_min_component), return_index=True)
            N = N[idx] if N is not None and len(N) else N
            S = S[idx] if S is not None and len(S) else S
        if S is not None:  # sigmas above 50 mm mean "no confidence" and are written as 50 mm
            S = np.minimum(S, np.float32(SIGMA_NONE_MM)).astype(np.float32)
        files: dict[str, str] = {}
        from ohs.track.export import save_mesh  # noqa: PLC0415

        for ext in export:
            path = out / f"mesh.{ext}"
            save_mesh(path, V, F, N if ext != "stl" and N is not None and len(N) == len(V) else None,
                      quality=S if ext == "ply" and S is not None and len(S) == len(V) else None)
            files[f"mesh_{ext}"] = str(path)
        mesh_ms = 1e3 * (time.perf_counter() - t0)
        report["mesh"] = {
            "method": method, "n_vertices": int(len(V)), "n_faces": int(len(F)), "n_faces_before_cleaning": n_faces_raw,
            "voxel_mm": float(pipe.fusion_params.voxel_mm),
            "bbox_mm": [V.min(0).tolist(), V.max(0).tolist()] if len(V) else None,
            "extract_ms": mesh_ms, "empty": len(F) == 0,
            "reconstruction": pipe.recon_status,
            "sigma_mm": sigma_stats(S, V, F),
        }
        if len(F) == 0:
            log.warning("the fused mesh is empty (no frame was tracked/fused?)")

        # laser cloud
        cloud = pipe.laser_cloud()
        lp = out / "laser_points.ply"
        cloud.save_ply(lp, voxel_mm=float(p.laser_voxel_mm))
        files["laser_points"] = str(lp)
        report["lasers"] = {
            "planes": sorted(pipe.planes),
            "n_points_raw": {name: int(len(cloud.raw_points([name]))) for name in cloud.names},
            "n_points_exported": int(len(cloud.points(float(p.laser_voxel_mm)))),
            "voxel_mm": float(p.laser_voxel_mm),
            "frames_with_points": cloud.n_frames,
            # dropped because they disagreed with their frame's dense depth (reflections, glints)
            "n_rejected_by_depth": {name: int(n) for name, n in pipe.laser_rejected.items()},
        }

        tp = out / "trajectory.csv"
        _write_trajectory(tp, traj_rows)
        files["trajectory"] = str(tp)

        gt = reader.ground_truth_poses()
        if gt:
            report["ground_truth"] = trajectory_errors(gt, [(i, T, ok) for i, _, ok, T in traj_rows])

        report["params"] = {
            "pipeline": {"mode": p.pipeline.mode, "scale": p.pipeline.scale, "use_imu": p.pipeline.use_imu,
                         "use_lasers": p.pipeline.use_lasers, "integrate_depth": p.pipeline.integrate_depth},
            "depth": asdict(pipe.depth_params), "laser": asdict(pipe.laser_params),
            "fusion": asdict(pipe.fusion_params),
            "tracking": ({"model": "none: fused at the given poses"} if poses is not None
                         else {"model": "separate (filtered depth)", "filter_px": pipe.track_filter_px,
                               "voxel_mm": pipe.track_fusion_params.voxel_mm,
                               "gyro_sigma_deg": pipe.track_fusion_params.gyro_sigma_deg,
                               # a detailed part: from this frame on, raw depth where the filter drops it
                               "detailed_part_from_frame": getattr(pipe.tracker, "detail_frame", None)}
                         if pipe.dual_model else {"model": "reconstruction TSDF (raw depth)"}
                         if p.pipeline.mode == "markerless" else {"model": "markers"}),
            "markers": asdict(pipe.marker_params) if pipe.markers is not None else None,
            "frame_step": p.frame_step, "export": list(export), "poisson": p.poisson,
            "clean_min_component": p.clean_min_component,
        }
        # where the work ran (M3): the GPU or the CPU, per stage
        de = getattr(pipe, "depth_estimator", None)
        report["params"]["compute"] = {
            "depth": getattr(de, "backend", None), "depth_device": getattr(de, "device", None),
            "tracking_model": getattr(pipe, "model_backend", None), "tracking_device": getattr(pipe, "model_device", None),
        }
        report["per_frame"] = rows
        files["report"] = str(out / "report.json")
        report["files"] = files
        report["wall_time_s"] = time.perf_counter() - wall0
        report = _jsonable(report)
        (out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    finally:
        pipe.close()
    fr = report["frames"]
    log.info("processed %d frames (%d tracked, %.0f %%) in %.1f s -> %s", fr["processed"], fr["tracked"],
             100.0 * fr["tracked_fraction"], report["wall_time_s"], out)
    return report
