"""Merging aligned scans into one model, from their raw recordings (research plan 4.7.4).

Once every scan has a pose in a common world (:func:`ohs.align.pairwise.align_pair`), every
frame of every scan has one too: the scan's pose times the frame's pose in its scan. All frames
are then fused into one model, as one scan's frames are, and meshed once. Nothing is stitched
from meshes, so there are no seams, overlaps are averaged by their weights, and the confidence
map is one map. It costs about as much as processing every scan again.

:func:`merge_scans` writes into ``out_dir``: ``mesh.ply`` (with each vertex's sigma when the
confidence model made it) and the other formats asked for, ``laser_points.ply``, and
``report.json`` with what went in (per scan: recording, pose, frames fused) and what came out.

Limits of this first version: all scans must have been taken with the same calibration and
image size (one scanner, one camera mode; a session after a recalibration is refused); the
frames are fused at the poses given, without a joint refinement across the scans; and Poisson
meshing is not offered (``ProcessParams.poisson`` is refused, not ignored).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from ohs.app.pipeline import FramePipeline
from ohs.app.process import READ_AHEAD_FRAMES, ProcessParams, _check_export, _jsonable
from ohs.io.recording import RecordingReader
from ohs.mesh.confidence import SIGMA_NONE_MM, sigma_stats
from ohs.track.posegraph import read_trajectory

__all__ = ["ScanInput", "merge_scans"]

log = logging.getLogger(__name__)


@dataclass
class ScanInput:
    """One scan to merge: its recording, its frames' poses in its own world (the
    ``trajectory.csv`` its processing wrote, or rows ``(index, t, ok, T_wc)``), and ``T``, the
    scan's world in the merged world (identity for the scan the others were aligned to).
    ``calib``: the calibration the scan was processed with, when that was not the recording's own
    (as ``process_recording(calib=...)``)."""

    recording: Path
    trajectory: Any
    T: np.ndarray = field(default_factory=lambda: np.eye(4))
    name: str | None = None
    calib: Any = None


def _same_calibration(a: Any, b: Any) -> str | None:
    """Why two calibrations are not the same scanner in the same mode, or None when they are."""
    if tuple(a.stereo.image_size) != tuple(b.stereo.image_size):
        return f"image sizes {tuple(a.stereo.image_size)} and {tuple(b.stereo.image_size)}"
    for key, va in vars(a.stereo).items():  # every number of the camera calibration, to the last digit
        vb = getattr(b.stereo, key, None)
        if isinstance(va, (np.ndarray, float, int)) and not isinstance(va, bool):
            if vb is None or np.shape(va) != np.shape(vb) or not np.allclose(va, vb, rtol=0, atol=1e-9):
                return "different camera calibrations"
    la, lb = a.lasers or {}, b.lasers or {}
    if set(la) != set(lb) or any(not np.allclose(la[k].normal, lb[k].normal, rtol=0, atol=1e-9)
                                 or abs(la[k].d - lb[k].d) > 1e-9 for k in la):
        return "different laser calibrations"
    return None


def merge_scans(scans: Sequence[ScanInput], out_dir: str | Path, params: ProcessParams | None = None,
                progress: Callable[[int, int], None] | None = None) -> dict:
    """Fuse the frames of all ``scans`` into one model and write it to ``out_dir`` (module
    docstring). ``params``: as for :func:`ohs.app.process.process_recording` (its pipeline
    settings, export formats and cleaning; nothing is tracked). ``progress(k, n)`` after each of
    the ``n`` frames. Returns the report (also ``report.json``). ``ValueError`` for no scans, a
    scan without a usable frame, or scans of different calibrations."""
    wall0 = time.perf_counter()
    if not scans:
        raise ValueError("no scans to merge")
    p = params if params is not None else ProcessParams()
    p = replace(p, pipeline=replace(p.pipeline, tracking=False, use_imu=False))
    export = _check_export(tuple(p.export))
    if p.poisson:
        raise ValueError("a merge cannot use Poisson meshing yet: leave ProcessParams.poisson off")
    out = Path(out_dir)
    plan = []
    calib = None
    for k, sc in enumerate(scans):
        name = sc.name or Path(sc.recording).name
        reader = RecordingReader(sc.recording)
        cal = sc.calib if sc.calib is not None else reader.calibration()
        if cal is None:
            raise ValueError(f"scan {name}: the recording has no calibration")
        if calib is None:
            calib = cal
        else:
            why = _same_calibration(calib, cal)
            if why is not None:
                raise ValueError(f"scan {name} was taken with another calibration than the first scan ({why}): "
                                 "scans of different calibrations cannot be merged yet")
        rows = read_trajectory(sc.trajectory) if isinstance(sc.trajectory, (str, Path)) else list(sc.trajectory)
        poses = {int(r[0]): np.asarray(r[3], dtype=np.float64).reshape(4, 4) for r in rows if r[2]}
        poses = {i: Tf for i, Tf in poses.items() if np.all(np.isfinite(Tf))}  # a lost frame, whatever its flag says
        positions = [pos for pos, idx in enumerate(reader.indices) if int(idx) in poses]
        if not positions:
            raise ValueError(f"scan {name}: none of its frames has a pose")
        T = np.asarray(sc.T, dtype=np.float64).reshape(4, 4)
        plan.append((name, reader, poses, positions, T))
    n = sum(len(pl[3]) for pl in plan)
    out.mkdir(parents=True, exist_ok=True)
    pipe = FramePipeline(calib, p.pipeline)
    per_scan = []
    k = 0
    try:
        for name, reader, poses, positions, T in plan:
            frames = reader.iter_ahead(positions, READ_AHEAD_FRAMES)
            try:
                for frame in frames:
                    pipe.process(frame, pose=T @ poses[int(frame.index)])
                    if progress is not None:
                        progress(k, n)
                    k += 1
            finally:
                frames.close()
            per_scan.append({"name": name, "recording": str(reader.path), "frames": len(positions), "T": T.tolist()})
            log.info("merged %s: %d frames", name, len(positions))
        V, F, N, S = pipe.mesh_with_sigma()
        n_faces_raw = int(len(F))
        if p.clean_min_component > 0 and len(F):
            from ohs.track.export import clean_mesh  # noqa: PLC0415

            V, F, idx = clean_mesh(V, F, min_component_fraction=float(p.clean_min_component), return_index=True)
            N = N[idx] if N is not None and len(N) else N
            S = S[idx] if S is not None and len(S) else S
        if S is not None:
            S = np.minimum(S, np.float32(SIGMA_NONE_MM)).astype(np.float32)
        from ohs.track.export import save_mesh, save_points_ply  # noqa: PLC0415

        files: dict[str, str] = {}
        for ext in export:
            path = out / f"mesh.{ext}"
            save_mesh(path, V, F, N if ext != "stl" and N is not None and len(N) == len(V) else None,
                      quality=S if ext == "ply" and S is not None and len(S) == len(V) else None)
            files[f"mesh_{ext}"] = str(path)
        cloud = pipe.laser_cloud()
        laser_pts = cloud.points(float(p.laser_voxel_mm))
        save_points_ply(out / "laser_points.ply", laser_pts)
        files["laser_points"] = str(out / "laser_points.ply")
        report: dict[str, Any] = {
            "merged": True, "out_dir": str(out), "scans": per_scan, "frames": {"fused": n},
            "image_size": list(pipe.image_size),
            "mesh": {"n_vertices": int(len(V)), "n_faces": int(len(F)), "n_faces_before_cleaning": n_faces_raw,
                     "voxel_mm": float(pipe.fusion_params.voxel_mm), "empty": len(F) == 0,
                     "bbox_mm": [V.min(0).tolist(), V.max(0).tolist()] if len(V) else None,
                     "reconstruction": pipe.recon_status, "sigma_mm": sigma_stats(S, V, F)},
            "lasers": {"planes": sorted(pipe.planes), "n_points_exported": int(len(laser_pts)),
                       "voxel_mm": float(p.laser_voxel_mm)},
            "files": files,
        }
        files["report"] = str(out / "report.json")
        report["wall_time_s"] = time.perf_counter() - wall0
        report = _jsonable(report)
        (out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    finally:
        pipe.close()
    log.info("merged %d scans, %d frames, in %.1f s -> %s", len(plan), n, report["wall_time_s"], out)
    return report
