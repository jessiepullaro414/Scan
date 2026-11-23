"""Make mesh's finer mesh detail on thin walls only (ENGINE_PLAN X5, step 2; ``ProcessParams.fine_walls``, off by
default). The engine's part is :mod:`ohs.mesh.fine_walls`; this is its place in the processing:

1. **Follow the camera keeps each fused frame's depth** (:class:`FusedDepthSpool`): the depth, its sigma and the pose
   exactly as the mesh's model fused them, in ``<stage>/fused_depth/`` (raw float32 on disk, about 8 MB a frame at
   1280 x 800: 1 GB for 120 frames; not in memory), with the model's settings. Only on the graphics card's path (the
   confidence model); without one nothing is kept and the step says so. The matcher, most of a frame's time, does not
   run again.
2. **After Straighten** (whose poses are then the scan's), :func:`make_fine_walls` reads the base stage's mesh, finds
   its thin walls (:func:`ohs.mesh.thin_parts.find_thin_walls`, the hint's own finding), fuses the kept depth at the
   base stage's poses into a 0.5 mm model limited to those places, and refines the mesh there
   (:func:`ohs.mesh.fine_walls.refine_onto`). A mesh without a thin wall is left exactly as it was (only the finding's
   time is spent). Refine with lasers, the recipe's edits, the Check and the report then work on the refined mesh, which
   carries a sigma and a count of looks for every vertex as before.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np

from ohs.mesh.fine_walls import FINE_VOXEL_MM, REGION_GROW_BRICKS, FineField, fine_bricks, in_bricks, refine_onto, region_bricks

__all__ = ["SPOOL_DIR", "FusedDepthSpool", "read_spool", "make_fine_walls", "spool_size_mb"]

log = logging.getLogger(__name__)

#: the folder of a stage (``process_recording``'s ``out_dir``) the kept depth goes to
SPOOL_DIR = "fused_depth"
VERSION = 1


def _model_kwargs(m: Any) -> dict:
    """The settings of a :class:`~ohs.track.recon_tsdf.ConfidenceTsdf` that make another one like it."""
    return {"K": np.asarray(m.K, dtype=np.float64).tolist(), "image_size": list(m.image_size),
            "voxel_mm": float(m.voxel_mm), "trunc_mm": float(m.trunc_mm), "depth_min_mm": float(m.depth_min_mm),
            "depth_max_mm": float(m.depth_max_mm), "look_px": m._look_px_given,
            "hole_factor": [float(m.hole_amp), float(m.hole_r_px)], "gap_guard_px": float(m.gap_guard_px),
            "back_mm": m.back_mm, "opposite_mm": m.opposite_mm, "opposite_shares": list(m.opposite_shares),
            "sigma_guard_mm": m.sigma_guard_mm, "sigma_guard_px": float(m.sigma_guard_px),
            "sigma_guard_angle": bool(m.sigma_guard_angle)}


def fine_model(kw: dict, voxel_mm: float = FINE_VOXEL_MM, backend: str = "gpu") -> Any:
    """A model like the one ``kw`` describes (:func:`_model_kwargs`) at mesh detail ``voxel_mm``: the lengths given in
    mesh detail (the truncation, the band behind the surface, the second mean's cut) scaled with it, as the pipeline
    makes its model at that mesh detail."""
    from ohs.track.recon_tsdf import ConfidenceTsdf  # noqa: PLC0415

    r = float(voxel_mm) / float(kw["voxel_mm"])
    return ConfidenceTsdf(np.asarray(kw["K"], dtype=np.float64), tuple(kw["image_size"]), voxel_mm=float(voxel_mm),
                          trunc_mm=float(kw["trunc_mm"]) * r, depth_min_mm=kw["depth_min_mm"],
                          depth_max_mm=kw["depth_max_mm"], backend=backend, look_px=kw["look_px"],
                          hole_factor=tuple(kw["hole_factor"]), gap_guard_px=kw["gap_guard_px"],
                          back_mm=None if kw["back_mm"] is None else float(kw["back_mm"]) * r,
                          opposite_mm=None if kw["opposite_mm"] is None else float(kw["opposite_mm"]) * r,
                          opposite_shares=tuple(kw["opposite_shares"]), sigma_guard_mm=kw["sigma_guard_mm"],
                          sigma_guard_px=kw["sigma_guard_px"], sigma_guard_angle=kw["sigma_guard_angle"])


class FusedDepthSpool:
    """Each fused frame's depth (and sigma, and the depth as seen when some was left out) kept on disk, in the order
    the model fused them (module docstring). ``add`` is called by the pipeline as it fuses; ``close`` writes the index
    (each entry's frame index and pose)."""

    def __init__(self, folder: str | Path, model: Any) -> None:
        self.folder = Path(folder)
        if self.folder.exists():
            shutil.rmtree(self.folder)
        self.folder.mkdir(parents=True)
        self.kwargs = _model_kwargs(model)
        self.entries: list[dict] = []
        self.bytes = 0
        self.write_s = 0.0

    def add(self, pos: int, depth: np.ndarray, sigma: np.ndarray | None, seen: np.ndarray | None, T_wc: np.ndarray) -> None:
        t0 = time.perf_counter()
        k = len(self.entries)
        with open(self.folder / f"{k:06d}.bin", "wb") as f:
            for a in (depth, sigma, seen):
                if a is not None:
                    b = np.ascontiguousarray(a, dtype=np.float32)
                    b.tofile(f)
                    self.bytes += b.nbytes
        self.entries.append({"pos": int(pos), "sigma": sigma is not None, "seen": seen is not None,
                             "T": np.asarray(T_wc, dtype=np.float64).reshape(4, 4).tolist()})
        self.write_s += time.perf_counter() - t0

    def close(self, frame_index_of_pos: list[int]) -> dict:
        """Write the index; ``frame_index_of_pos``: the recording's frame index of each place in the trajectory."""
        for e in self.entries:
            e["index"] = int(frame_index_of_pos[e["pos"]]) if 0 <= e["pos"] < len(frame_index_of_pos) else None
        head = {"version": VERSION, "model": self.kwargs, "entries": self.entries}
        (self.folder / "index.json").write_text(json.dumps(head), encoding="utf-8")
        return {"kept": True, "frames": len(self.entries), "mb": round(self.bytes / 2 ** 20, 1),
                "write_s": round(self.write_s, 2)}


#: what a kept frame takes on disk, a pixel of the processed image: its depth and its sigma, 4 bytes each (the depth as
#: seen before a clip plane comes on top only with one; Make mesh has none): 7.8 MB a frame at 1280 x 800
KEPT_BYTES_PER_PIXEL = 8


def kept_depth_estimate(rec_path: str | Path, params: Any) -> dict:
    """What Follow the camera with ``params`` (``ProcessParams``, :func:`ohs.app.process.process_recording`) keeps on
    disk with ``fine_walls``, estimated before it runs: ``{frames, bytes_per_frame, bytes}``. ``frames``: the frames it
    will process (the step, the rate limit, the start and stop and the cut intervals as the pass picks them; frames
    picked by motion can only be fewer), so never fewer than it keeps; ``bytes_per_frame``: :data:`KEPT_BYTES_PER_PIXEL`
    times the processed image's pixels (the calibration's image size at the pipeline's scale)."""
    from ohs.app.process import cut_intervals  # noqa: PLC0415
    from ohs.io.recording import RecordingReader, effective_frame_step  # noqa: PLC0415

    reader = RecordingReader(rec_path)
    calib = reader.calibration()
    scale = float(params.pipeline.scale)
    if calib is not None:
        size = calib.stereo.image_size if scale == 1.0 else calib.stereo.scaled(scale).image_size
    else:
        raw = reader.meta.get("image_size") or list(reader.frame(0).image_size)
        size = (int(round(raw[0] * scale)), int(round(raw[1] * scale)))
    w, h = int(size[0]), int(size[1])
    stop = len(reader) if params.stop is None else min(int(params.stop), len(reader))
    times = reader.times()
    step, _ = effective_frame_step(times, int(params.frame_step), float(params.max_frame_rate))
    positions = list(range(max(0, int(params.start)), stop, step))
    cuts = cut_intervals(reader)
    if cuts and positions:
        t = times[np.asarray(positions, dtype=np.int64)]
        inside = np.zeros(len(positions), dtype=bool)
        for a, b in cuts:
            inside |= (t >= a) & (t <= b)
        positions = [p for p, c in zip(positions, inside) if not c]
    bpf = KEPT_BYTES_PER_PIXEL * w * h
    return {"frames": len(positions), "bytes_per_frame": int(bpf), "bytes": int(bpf * len(positions))}


def spool_size_mb(folder: str | Path) -> float:
    p = Path(folder)
    return sum(f.stat().st_size for f in p.glob("*.bin")) / 2 ** 20 if p.is_dir() else 0.0


def read_spool(folder: str | Path) -> dict | None:
    """The kept depth's index (:class:`FusedDepthSpool`), or None when there is none."""
    p = Path(folder) / "index.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _frame(folder: Path, k: int, e: dict, shape: tuple[int, int]) -> tuple:
    a = np.fromfile(folder / f"{k:06d}.bin", dtype=np.float32)
    n = shape[0] * shape[1]
    depth = a[:n].reshape(shape)
    o = n
    sigma = seen = None
    if e["sigma"]:
        sigma = a[o:o + n].reshape(shape)
        o += n
    if e["seen"]:
        seen = a[o:o + n].reshape(shape)
    return depth, sigma, seen


def _poses_of(stage_dir: Path) -> dict[int, np.ndarray]:
    from ohs.track.posegraph import read_trajectory  # noqa: PLC0415

    return {int(i): np.asarray(T, dtype=np.float64) for i, _, ok, T in read_trajectory(stage_dir / "trajectory.csv") if ok}


def make_fine_walls(stage_dir: str | Path, spool_dir: str | Path, out_dir: str | Path | None = None,
                    poses: dict[int, np.ndarray] | None = None, cancelled: Any = None, backend: str = "gpu",
                    progress: Any = None) -> dict:
    """Refine the stage's mesh (``<stage_dir>/mesh.ply``, made at its ``report.json``'s mesh detail) on its thin walls
    with the depth kept in ``spool_dir`` (module docstring), fused at ``poses`` (frame index -> ``T_wc``; None: the
    stage's ``trajectory.csv``). The refined mesh is written to ``<out_dir>/mesh.ply`` (``out_dir`` None: the stage's
    own) only when it changed; returns the report block (``report.json``'s ``fine_walls``): ``acted`` whether the mesh
    changed, ``why`` when not, the walls found, the regions, the frames, the surface made finer, the moves, the seam,
    the triangles and open loops before and after, the times. ``progress(fraction, message)``: how far it is (the
    finding to 0.15, fusing the kept frames to 0.75, the stitch to 1); ``cancelled()``: True stops it
    (``InterruptedError``) between two frames."""
    say = progress or (lambda f, m: None)
    from ohs.mesh.thin_parts import BRICK_VOXELS, find_thin_walls  # noqa: PLC0415
    from ohs.track.export import load_ply, save_mesh  # noqa: PLC0415

    t0 = time.perf_counter()
    stage_dir, spool_dir = Path(stage_dir), Path(spool_dir)
    out: dict[str, Any] = {"version": VERSION, "fine_voxel_mm": FINE_VOXEL_MM, "acted": False}
    ms: dict[str, float] = {}
    out["ms"] = ms
    rep = json.loads((stage_dir / "report.json").read_text(encoding="utf-8")) if (stage_dir / "report.json").is_file() else {}
    head = read_spool(spool_dir)
    v = float(((rep.get("mesh") or {}).get("voxel_mm")) or (head or {}).get("model", {}).get("voxel_mm") or 0.0)
    out["voxel_mm"] = v
    if head is None:
        # the depth is kept on the graphics card's path only (the confidence model): the stage's report says why not
        kept = rep.get("fine_walls_depth") or {}
        out["why"] = "no_graphics_card" if kept.get("why") == "no_graphics_card" else "no_kept_depth"
        log.info("fine mesh detail on thin walls: no kept depth (%s): the mesh stays at %.2g mm", out["why"], v)
        return out
    if not v > FINE_VOXEL_MM + 1e-9:
        out["why"] = "made_fine"
        return out
    d = load_ply(stage_dir / "mesh.ply")
    V = np.asarray(d["vertices"], dtype=np.float64)
    F = np.zeros((0, 3), np.int64) if d["faces"] is None else np.asarray(d["faces"], dtype=np.int64).reshape(-1, 3)
    N, S, L = d.get("normals"), d.get("quality"), d.get("looks")
    t = time.perf_counter()
    say(0.0, "Finding the thin walls")
    opposite = ((rep.get("params") or {}).get("reconstruction") or {}).get("opposite_mm") is not None
    walls, wv = find_thin_walls(V, F, voxel_mm=v, normals=N, sigma=S, opposite_sides=opposite)
    ms["find"] = round(1e3 * (time.perf_counter() - t), 0)
    out["walls"] = {k: walls.get(k) for k in ("found", "area_mm2", "share", "reads_mm", "reads_range_mm", "n_regions")}
    if not walls.get("found"):
        out["why"] = "no_thin_walls"
        ms["total"] = round(1e3 * (time.perf_counter() - t0), 0)
        return out
    brick = BRICK_VOXELS * v
    R = region_bricks(V[wv], brick, REGION_GROW_BRICKS)
    flagged = region_bricks(V[wv], brick, 0)
    allowed = fine_bricks(R, brick, 8 * FINE_VOXEL_MM)
    out["bricks"] = {"size_mm": round(brick, 3), "flagged": int(len(flagged)), "grown_by": REGION_GROW_BRICKS,
                     "region": int(len(R)), "fine_size_mm": round(8 * FINE_VOXEL_MM, 3), "fine_allowed": int(len(allowed))}
    if poses is None:
        poses = _poses_of(stage_dir)
    # the finer model, fed the kept depth at the stage's poses, on the region's bricks only
    t = time.perf_counter()
    try:
        model = fine_model(head["model"], FINE_VOXEL_MM, backend=backend)
    except RuntimeError:  # the depth was kept on a graphics card that is gone now
        out["why"] = "no_graphics_card"
        log.info("fine mesh detail on thin walls: no graphics card now: the mesh stays at %.2g mm", v)
        return out
    try:
        model.limit_bricks(allowed)
        w, h = head["model"]["image_size"]
        n_used = 0
        n_all = max(len(head["entries"]), 1)
        say(0.15, "Fusing the kept frames finer on the thin walls")
        for k, e in enumerate(head["entries"]):
            if cancelled is not None and cancelled():
                raise InterruptedError("cancelled")
            if k % 10 == 0:
                say(0.15 + 0.6 * k / n_all, f"Fusing the kept frames finer on the thin walls: frame {k + 1:,} of {n_all:,}")
            i = e.get("index")
            T = poses.get(int(i)) if i is not None else None
            if T is None:
                continue
            depth, sigma, seen = _frame(spool_dir, k, e, (int(h), int(w)))
            model.integrate(depth, sigma, T, seen=seen)
            n_used += 1
        ms["fuse"] = round(1e3 * (time.perf_counter() - t), 0)
        t = time.perf_counter()
        field = FineField.from_model(model)
        out["fine_model"] = {"bricks": int(field.n_bricks), "mb": round(model.memory_mb, 1), "device": model.device}
        ms["download"] = round(1e3 * (time.perf_counter() - t), 0)
    finally:
        model.close()
    out["frames"] = {"kept": len(head["entries"]), "fused": n_used}
    if cancelled is not None and cancelled():
        raise InterruptedError("cancelled")
    say(0.75, "Moving the thin walls onto the finer surface")
    t = time.perf_counter()
    inside = in_bricks(V, R, brick)
    region = inside[F].all(axis=1) if len(F) else np.zeros(0, dtype=bool)
    # the split is conforming, so the open loops stay the same by construction (the tests count them; the scorecard
    # counts them on every run): not counted here, which saves a second on a million triangles
    (V2, F2, N2, S2, L2), r = refine_onto(V, F, N, S, L, region, field, coarse_voxel_mm=v, count_loops=False)
    ms["refine"] = round(1e3 * (time.perf_counter() - t), 0)
    out.update({k: r.get(k) for k in ("faces_in", "area_mm2", "share", "triangles_before", "triangles_after", "vertices",
                                      "moved_mm", "seam_mm", "seam_vertices", "loops", "limits")})
    out["acted"] = bool(r.get("changed"))
    if not out["acted"]:
        out["why"] = "no_finer_surface"  # the finer model held no surface near the mesh: it stays as it was
    else:
        t = time.perf_counter()
        dst = stage_dir if out_dir is None else Path(out_dir)
        dst.mkdir(parents=True, exist_ok=True)
        save_mesh(dst / "mesh.ply", V2, F2, None if N2 is None else N2, quality=S2, looks=L2)
        ms["save"] = round(1e3 * (time.perf_counter() - t), 0)
    ms["total"] = round(1e3 * (time.perf_counter() - t0), 0)
    say(1.0, "The thin walls are done")
    log.info("fine mesh detail on thin walls: %s (%s)", "made finer" if out["acted"] else "unchanged",
             {k: out.get(k) for k in ("walls", "bricks", "frames", "faces_in", "triangles_after", "ms")})
    return out
