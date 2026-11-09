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
Malformed cut values are ignored with a warning. Marker mode calls ``MarkerTracker.refine_map()`` after the last frame and saves the
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
from ohs.geometry import invert_se3, pose_error
from ohs.io.recording import RecordingReader
from ohs.types import ScannerCalibration

__all__ = ["ProcessParams", "process_recording", "trajectory_errors", "cut_intervals", "EXPORT_FORMATS"]

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
    if res.laser_points:
        row["n_laser"] = {k: int(len(v)) for k, v in res.laser_points.items()}
    return row


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
        # nothing is tracked: a single (reconstruction) model and no gyro prior
        p = replace(p, pipeline=replace(p.pipeline, track_filter_px=0.0, use_imu=False))
    export = _check_export(tuple(p.export))
    if int(p.frame_step) < 1:
        raise ValueError("frame_step must be >= 1")
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
        positions = [pos for pos, cut in zip(positions, inside) if not cut]
        log.info("skipping %d frames inside %d cut interval(s)", n_cut, len(cuts))
        if not positions:
            raise ValueError(f"every selected frame of {rec_path} lies inside a cut (rewound) interval: nothing to process")
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
                "start": int(p.start), "stop": stop, "step": int(p.frame_step), "cut": n_cut,
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
        V, F, N = pipe.mesh()
        if p.poisson:
            from ohs.track.export import poisson_mesh  # noqa: PLC0415 (open3d)

            P, Np = pipe.points()
            if len(P) >= 10:
                V, F, N = poisson_mesh(P, Np, depth=int(p.poisson_depth))
                method = "poisson"
            else:
                log.warning("too few TSDF points for Poisson meshing; keeping the marching-cubes mesh")
        n_faces_raw = int(len(F))
        if p.clean_min_component > 0 and len(F):
            from ohs.track.export import clean_mesh  # noqa: PLC0415

            V, F, idx = clean_mesh(V, F, min_component_fraction=float(p.clean_min_component), return_index=True)
            N = N[idx] if N is not None and len(N) else N
        files: dict[str, str] = {}
        from ohs.track.export import save_mesh  # noqa: PLC0415

        for ext in export:
            path = out / f"mesh.{ext}"
            save_mesh(path, V, F, N if ext != "stl" and N is not None and len(N) == len(V) else None)
            files[f"mesh_{ext}"] = str(path)
        mesh_ms = 1e3 * (time.perf_counter() - t0)
        report["mesh"] = {
            "method": method, "n_vertices": int(len(V)), "n_faces": int(len(F)), "n_faces_before_cleaning": n_faces_raw,
            "voxel_mm": float(pipe.fusion_params.voxel_mm),
            "bbox_mm": [V.min(0).tolist(), V.max(0).tolist()] if len(V) else None,
            "extract_ms": mesh_ms, "empty": len(F) == 0,
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
                               "gyro_sigma_deg": pipe.track_fusion_params.gyro_sigma_deg}
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
