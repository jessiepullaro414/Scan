"""Merging aligned scans into one model, from their raw recordings (research plan 4.7.4).

Once every scan has a pose in a common world (:func:`ohs.align.pairwise.align_pair`), every
frame of every scan has one too: the scan's pose times the frame's pose in its scan. All frames
are then fused into one model, as one scan's frames are, and meshed once. Nothing is stitched
from meshes, so there are no seams, overlaps are averaged by their weights, and the confidence
map is one map. It costs about as much as processing every scan again.

:func:`merge_scans` writes into ``out_dir``: ``mesh.ply`` (with each vertex's sigma and its count of
independent looks when the confidence model made it) and the other formats asked for,
``laser_points.ply``, and ``report.json`` with what went in (per scan: recording, pose, frames fused, and what of its table
was left out and why: ``scans[i].table``), what came out, and ``warnings`` (each naming its scan). When a GPU stage
failed during the merge (the depth matcher falling back to the processor's: each frame is still fused with the factor
of the matcher that made its depth), ``compute.fallbacks`` lists it (scan, frame, stage, from, to, reason) and a
warning says it; without one there is no ``compute`` block.

**A part that was moved** between the scans (turned over, set down elsewhere) lay on a table
that is in a different place, relative to the part, in each scan, and each scan's table must stay
out of the merged model, and nothing else. Give each scan its ``support`` then: the map of its
table that its own mesh gives (:func:`scan_support`; :class:`ohs.mesh.support.SupportMap`, whose
module docstring, 4., states the rule). Each frame keeps its depth and laser points where the part
stands (down to the table, so a part's foot and a thin part lying flat stay, whatever their
thickness) and loses them on the bare table up to 2 mm above it; what the frames leave of the table
(a ring at the part's foot, crumbs) is taken out of the merged mesh by the single-scan edit's rule,
and counted in the report. Fusing everything and removing the tables from the merged mesh
afterwards is not sound: where the part was turned over, one scan's table lies in space another
scan saw as empty, so the fusion half-erases it and what is left touches the part.

When a scan's table cannot be found (:func:`ohs.mesh.support.find_support` refuses), the merge does
not guess: that scan is clipped as before this map existed, by the alignment's support plane
(``support`` given as a plane ``(n, d)``: everything on or behind it and up to
``support_margin_mm`` in front of it, with the part's bottom edge), or not at all when there is no
such plane, and a warning names the scan. When the part was not moved the table is one surface in
all scans and stays in: no ``support``, and the merge is a plain fusion of the frames.

**Scans of different calibrations** (a session after a recalibration, another camera mode, or
another scanner) merge too: each scan's frames go through a pipeline of its own calibration
(rectified, matched and triangulated as that scan was), and all of them fuse into one model,
which is handed from pipeline to pipeline (:meth:`ohs.app.pipeline.FramePipeline.release_model`).
That model's grid is the finest of the scans' own (``ProcessParams.pipeline.fusion`` unset: 1 mm
when one scan is at full resolution); ``pipeline.scale`` applies to every scan alike. The report
says which calibration each scan had (``scans[i].calibration``, ``calibrations``).

Limits: the frames are fused at the poses given, without a joint refinement across the scans;
and Poisson meshing is not offered (``ProcessParams.poisson`` is refused, not ignored).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from ohs.app.pipeline import FramePipeline, default_fusion_params, effective_scale
from ohs.app.process import READ_AHEAD_FRAMES, ProcessParams, _check_export, _jsonable
from ohs.io.recording import RecordingReader
from ohs.mesh import support as _support
from ohs.mesh.confidence import SIGMA_NONE_MM, sigma_stats
from ohs.mesh.support import SupportMap
from ohs.track.posegraph import read_trajectory
from ohs.vision.rectify import Rectifier

__all__ = ["ScanInput", "merge_scans", "scan_support", "scan_support_from_mesh"]

log = logging.getLogger(__name__)

SLAB_MM = 2.0  # a scan whose table was not found: left out up to this far in front of the alignment's support plane


@dataclass
class ScanInput:
    """One scan to merge: its recording, its frames' poses in its own world (the
    ``trajectory.csv`` its processing wrote, or rows ``(index, t, ok, T_wc)``), and ``T``, the
    scan's world in the merged world (identity for the scan the others were aligned to).
    ``calib``: the calibration the scan was processed with, when that was not the recording's own
    (as ``process_recording(calib=...)``). ``support``, when the part was moved (module docstring):
    the map of the scan's table (:func:`scan_support`), or a plane ``(n, d)`` in the scan's own world
    with ``n`` towards the part (:attr:`ohs.align.pairwise.ScanCloud.support`): nothing of that scan on
    or behind it, nor up to ``support_margin_mm`` in front, goes into the merged model. ``warnings``:
    what the merge's report says of this scan's table besides (why it is left out as it is)."""

    recording: Path
    trajectory: Any
    T: np.ndarray = field(default_factory=lambda: np.eye(4))
    name: str | None = None
    calib: Any = None
    support: SupportMap | tuple[np.ndarray, float] | None = None
    warnings: list[str] = field(default_factory=list)


def scan_support(V: np.ndarray, F: np.ndarray, *, normals: np.ndarray | None = None, sigma: np.ndarray | None = None,
                 name: str | None = None) -> tuple[SupportMap | tuple[np.ndarray, float] | None, list[str]]:
    """What of a scan to leave out of a merge when the part was moved, from the scan's mesh ``(V, F)``
    (``normals``, ``sigma``: as :func:`ohs.mesh.support.find_support` takes them): ``(support, warnings)``
    for :class:`ScanInput`. The map of its table (module docstring); or, when no table can be told
    apart on the mesh, the alignment's support plane (the old slab, with the part's bottom edge) and a
    warning naming the scan, or nothing at all, also said."""
    from ohs.align.pairwise import scan_cloud  # noqa: PLC0415
    from ohs.mesh.topology import vertex_normals  # noqa: PLC0415

    who = f"\"{name}\"" if name else "a scan"
    try:
        f = _support.find_support(V, F, normals=normals, sigma=sigma)
    except _support.SupportRefused as e:
        why = str(e).split(" Nothing was removed", 1)[0].strip()
        why = why[len("No table found: "):] if why.startswith("No table found: ") else why
        N = normals if normals is not None else vertex_normals(*_as_mesh(V, F))
        try:
            plane = scan_cloud(np.asarray(V, dtype=np.float64), np.asarray(N, dtype=np.float64)).support
        except ValueError:
            plane = None
        if plane is not None:
            return plane, [f"No table could be told apart on {who}: {why} Its table is left out as before: everything within "
                           f"{SLAB_MM:g} mm of the largest flat surface the alignment found, and with it about {SLAB_MM:g} mm of "
                           f"the part's bottom edge (or of whatever that surface is), and any part lying flat on it that is "
                           f"thinner than {SLAB_MM:g} mm."]
        return None, [f"No table could be told apart on {who}: {why} Nothing of it is left out, so its table, if it has one, "
                      "is in the merged scan."]
    return _support.support_map(V, F, f), []


def _as_mesh(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    from ohs.mesh.topology import as_mesh  # noqa: PLC0415

    return as_mesh(V, F)


def scan_support_from_mesh(path: str | Path, name: str | None = None) -> tuple[SupportMap | tuple[np.ndarray, float] | None, list[str]]:
    """:func:`scan_support` of a processed scan's mesh file (``result/mesh.ply``, its sigma read from the
    vertices' quality)."""
    from ohs.track.export import load_ply  # noqa: PLC0415

    d = load_ply(path)
    V = d["vertices"]
    F = d["faces"] if d["faces"] is not None else np.zeros((0, 3), dtype=np.int64)
    S = d["quality"]
    return scan_support(V, F, sigma=S if S is not None and len(S) == len(V) else None, name=name)


def _table_entry(sup: Any, warnings: list[str]) -> dict | None:
    """What the report says of a scan's table, before the merge has run (its counts are added after)."""
    if isinstance(sup, SupportMap):
        e = {"rule": "map", **sup.to_dict()}
        e["warnings"] = list(warnings) + e["warnings"]
        return e
    if sup is not None:
        return {"rule": "slab", "plane": {"normal": [float(x) for x in sup[0]], "d": float(sup[1])}, "margin_mm": SLAB_MM,
                "warnings": list(warnings),
                "summary": f"Its table is left out as before this map existed: everything within {SLAB_MM:g} mm of the "
                           f"plane the alignment found, and with it about {SLAB_MM:g} mm of the part's bottom edge and any "
                           f"part lying flat on it that is thinner than {SLAB_MM:g} mm."}
    if warnings:
        return {"rule": "none", "warnings": list(warnings), "summary": "Nothing of it is left out: no table was found on its mesh."}
    return None


def _count(n: int, one: str, many: str | None = None) -> str:
    """``n`` and the noun in the right number: "1 triangle", "3 triangles", "1,204 triangles"."""
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _cleaning_summary(c: dict, fraction: float, moved: bool) -> str:
    """One sentence on what the merge's small-piece cleaning took (``c``: :func:`clean_merged`'s info); ``moved``:
    the part was moved (a piece over a thin part was kept whatever its size, and the sentence says how many)."""
    n_gone = int(c["pieces_removed"])
    rule = f"under {100 * fraction:g} % of the triangles each"
    s = (f"{_count(n_gone, 'small loose bit')} ({c['area_removed_mm2']:,.0f} mm²) {'was' if n_gone == 1 else 'were'} "
         f"left out as too small ({rule})" if n_gone else f"No loose bit was left out as too small ({rule})")
    if moved:
        k = int(c["thin_pieces_kept"])
        s += f"; {_count(k, 'small bit')} over a thin part {'was' if k == 1 else 'were'} kept"
    return s + "."


def _map_summary(e: dict) -> str:
    """One sentence on what a scan's table map left out and kept, with what the merged mesh lost of it.
    A thin part counts as kept only when the merge found it in its result (:func:`settle_thin_parts`)."""
    s = (f"Its table is left out ({(e.get('table_area_mm2') or 0.0):,.0f} mm² of its mesh, flat to "
         f"{e.get('flatness_mm') or 0.0:.2f} mm); the part is kept right down to it")
    kept = [x for x in e["thin_parts"] if x["verdict"] == "kept" and x.get("in_merge", "kept") == "kept"]
    warned = [x for x in e["thin_parts"] if x["verdict"] == "warned" and x.get("in_merge", "kept") == "kept"]
    lost = [x for x in e["thin_parts"] if x.get("in_merge", "kept") != "kept"]
    if kept:
        s += "; " + ("a thin part lying flat is kept (" if len(kept) == 1 else f"{len(kept)} thin parts lying flat are kept (")
        s += ", ".join(f"{x['size_mm']:.0f} mm across, {x['height_mm']:.2f} mm" for x in kept[:4]) + ")"
    if warned:
        s += (f"; {_count(len(warned), 'raised area')} that may be "
              f"{'a thin part is' if len(warned) == 1 else 'thin parts are'} kept too")
    if lost:
        s += (f"; {_count(len(lost), 'thin part or raised area', 'thin parts or raised areas')} found on its table "
              f"{'is' if len(lost) == 1 else 'are'} not whole in the merged scan (see the warnings)")
    left = e.get("left_in_mesh")
    if left and left["faces"]:
        s += (f". What the frames left of it next to the part, {_count(left['faces'], 'triangle')} "
              f"({left['area_mm2']:,.0f} mm²), was taken out of the merged scan")
    return s + "."


def _over_thin(V: np.ndarray, F: np.ndarray, supports: Sequence[tuple[SupportMap, np.ndarray]]) -> np.ndarray:
    """The faces of a merged mesh that lie over a scan's thin part (a raised area kept or warned about),
    near its table's plane (1 mm behind it to 3 mm in front), seen in that scan's world."""
    out = np.zeros(len(F), dtype=bool)
    if not len(F):
        return out
    C = (V[F[:, 0]] + V[F[:, 1]] + V[F[:, 2]]) / 3.0
    for m, T in supports:
        if not m.thin.any():
            continue
        Ti = np.linalg.inv(np.asarray(T, dtype=np.float64).reshape(4, 4))
        Cs = C @ Ti[:3, :3].T + Ti[:3, 3]
        h = m.heights(Cs)
        near = (h >= -1.0) & (h <= 3.0)
        out[near] |= m.at(m.thin, Cs[near])
    return out


def clean_merged(V: np.ndarray, F: np.ndarray, supports: Sequence[tuple[SupportMap, np.ndarray]],
                 min_component_fraction: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """The merge's cleaning (:func:`ohs.track.export.clean_mesh`: degenerate faces, then pieces joined by
    their vertices with fewer than ``min_component_fraction`` of the faces) for a merge of a moved part:
    a piece that lies over a scan's thin part (``supports``: each moved scan's map and its world in the
    mesh's) is not cleaned away, whatever its size. ``(V, F, kept vertex indices, info)``, ``info``: the
    pieces kept for being thin parts, and the pieces removed and their area."""
    from scipy.sparse import coo_matrix  # noqa: PLC0415
    from scipy.sparse.csgraph import connected_components  # noqa: PLC0415

    from ohs.mesh.topology import face_areas  # noqa: PLC0415

    V = np.asarray(V)
    F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
    F = F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])]
    info = {"thin_pieces_kept": 0, "pieces_removed": 0, "area_removed_mm2": 0.0}
    if len(F) == 0:
        return V[:0], F[:0], np.zeros(0, dtype=np.int64), info
    rows = np.concatenate([F[:, 0], F[:, 1], F[:, 2]])
    cols = np.concatenate([F[:, 1], F[:, 2], F[:, 0]])
    A = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(len(V), len(V)))
    n_comp, labels = connected_components(A, directed=False)
    comp = labels[F[:, 0]]
    counts = np.bincount(comp, minlength=n_comp)
    big = counts >= float(min_component_fraction) * len(F)
    thin = np.zeros(n_comp, dtype=bool)
    over = _over_thin(V, F, supports)
    thin[comp[over]] = True
    keep = big | thin
    present = counts > 0
    info["thin_pieces_kept"] = int((thin & ~big).sum())
    gone = present & ~keep
    info["pieces_removed"] = int(gone.sum())
    info["area_removed_mm2"] = float(face_areas(V, F)[gone[comp]].sum()) if gone.any() else 0.0
    F = F[keep[comp]]
    used = np.unique(F)
    remap = np.full(len(V), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return V[used], remap[F], used, info


def settle_thin_parts(e: dict, m: SupportMap, V: np.ndarray, F: np.ndarray, T: np.ndarray) -> None:
    """Say in a scan's report entry ``e`` what of each of its thin parts (kept or warned about) is in the
    merged mesh ``(V, F)`` (``T``: the scan's world in the mesh's): ``in_merge`` "kept" (half or more of
    its cells have merged surface over them, near the table's plane), "in part" or "missing", and
    ``in_merge_share``; where it is not kept, its warning says so instead of "it is kept in the merged
    scan", and a thin part the scan kept gets a warning of its own. The merge means to keep them all,
    but the fusion can still lose one: another scan may have seen through where it lay, or the scan's
    depth had holes round it (the gap guard, ``ohs.track.recon_tsdf``, leaves out the depth next to a
    hole; it judges the frame as the camera saw it, so the table the merge leaves out is no hole to it)."""
    if not e.get("thin_parts"):
        return
    covered = np.zeros(m.thin.size, dtype=bool)
    if len(F):
        Ti = np.linalg.inv(np.asarray(T, dtype=np.float64).reshape(4, 4))
        Vs = np.asarray(V, dtype=np.float64) @ Ti[:3, :3].T + Ti[:3, 3]
        C = Vs[F].mean(axis=1)
        h = m.heights(C)
        near = (h >= -1.0) & (h <= 3.0)
        g = np.zeros(m.thin.shape, dtype=bool)
        _support._mark(g, m, Vs, F[near])
        covered = g.ravel()
    for x, cells in zip(e["thin_parts"], m.thin_cells):
        share = float(covered[cells].mean()) if len(cells) else 0.0
        x["in_merge_share"] = share
        x["in_merge"] = "kept" if share >= 0.5 else "in part" if share >= 0.05 else "missing"
        if x["in_merge"] == "kept":
            continue
        what = ("only {:.0f} % of it is in the merged scan".format(100 * share) if x["in_merge"] == "in part"
                else "it is not in the merged scan")
        why = ("; the fusion lost it: another scan saw through where it lay, or the scan's own depth had holes there "
               "(the fusion leaves out the depth next to a hole)")
        if x["verdict"] == "warned":
            new = (f"A raised area {x['size_mm']:.0f} mm across, {x['height_mm']:.2f} mm above the table round it, "
                   f"{x.get('where', '')}, may be a thin part: the merge kept it, but {what}{why}.")
            old = x.get("warning")
            e["warnings"] = [new if w == old else w for w in e["warnings"]]
            if old not in e["warnings"] and new not in e["warnings"]:
                e["warnings"].append(new)
            x["warning"] = new
        else:
            e["warnings"].append(f"A thin part lying flat, {x['size_mm']:.0f} mm across and {x['height_mm']:.2f} mm high, "
                                 f"{x.get('where', '')}, was found on this scan's table and kept, but {what}{why}.")


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
                progress: Callable[[int, int], None] | None = None, support_margin_mm: float = 2.0) -> dict:
    """Fuse the frames of all ``scans`` into one model and write it to ``out_dir`` (module
    docstring). ``params``: as for :func:`ohs.app.process.process_recording` (its pipeline
    settings, export formats and cleaning; nothing is tracked). ``progress(k, n)`` after each of
    the ``n`` frames. Returns the report (also ``report.json``). ``ValueError`` for no scans, or a
    scan without a calibration or without a usable frame."""
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
    tables: list[dict | None] = []  # what each scan's table entry in the report says
    cals: list[Any] = []  # the scans' calibrations, each one once, in the order of first use
    for k, sc in enumerate(scans):
        name = sc.name or Path(sc.recording).name
        reader = RecordingReader(sc.recording)
        cal = sc.calib if sc.calib is not None else reader.calibration()
        if cal is None:
            raise ValueError(f"scan {name}: the recording has no calibration")
        ci = next((i for i, c in enumerate(cals) if _same_calibration(c, cal) is None), None)
        if ci is None:
            cals.append(cal)
            ci = len(cals) - 1
        rows = read_trajectory(sc.trajectory) if isinstance(sc.trajectory, (str, Path)) else list(sc.trajectory)
        poses = {int(r[0]): np.asarray(r[3], dtype=np.float64).reshape(4, 4) for r in rows if r[2]}
        poses = {i: Tf for i, Tf in poses.items() if np.all(np.isfinite(Tf))}  # a lost frame, whatever its flag says
        positions = [pos for pos, idx in enumerate(reader.indices) if int(idx) in poses]
        if not positions:
            raise ValueError(f"scan {name}: none of its frames has a pose")
        T = np.asarray(sc.T, dtype=np.float64).reshape(4, 4)
        support = None
        if isinstance(sc.support, SupportMap):
            support = sc.support
        elif sc.support is not None:
            sn = np.asarray(sc.support[0], dtype=np.float64).reshape(3)
            support = (sn / np.linalg.norm(sn), float(sc.support[1]) / float(np.linalg.norm(sn)))
        tables.append(_table_entry(support, list(sc.warnings)))
        plan.append((name, reader, poses, positions, T, support, ci))
    n = sum(len(pl[3]) for pl in plan)
    pp = p.pipeline
    widths = [Rectifier(c.stereo, pp.scale).image_size[0] for c in cals]
    if len(cals) > 1 and pp.fusion is None:  # one model for all: the finest of the scans' own grids
        pp = replace(pp, fusion=default_fusion_params(effective_scale(max(widths))))
    out.mkdir(parents=True, exist_ok=True)
    pipe = FramePipeline(cals[plan[0][6]], pp)
    cur = plan[0][6]
    planes: set[str] = set()
    per_scan = []
    k = 0
    # the mesh model's pool (ENGINE_PLAN R2-5): the scan and frame at which it first had no room for new surface; one
    # model for the whole merge, so it is checked across the pipelines of every calibration
    full_at: dict | None = None
    # every GPU stage that failed during the merge, at its scan and frame (ENGINE_PLAN R2-7), as process_recording
    # saves them for a scan (ohs.app.process.fallback_entries)
    fallbacks: list[dict] = []
    from ohs.app.process import fallback_entries  # noqa: PLC0415
    try:
        for name, reader, poses, positions, T, support, ci in plan:
            if ci != cur:  # another calibration: its own pipeline, fusing into the same model
                planes |= set(pipe.planes)
                cloud, model = pipe.laser_cloud(), pipe.release_model()
                pipe.close()
                try:
                    pipe = FramePipeline(cals[ci], pp, model=model)
                except BaseException:
                    if model is not None:
                        model.close()
                    raise
                pipe.cloud = cloud
                cur = ci
            frames = reader.iter_ahead(positions, READ_AHEAD_FRAMES)
            try:
                for frame in frames:
                    Tf = poses[int(frame.index)]  # the frame in its scan's world
                    clip = None
                    if isinstance(support, SupportMap):  # the table's map, seen from this frame's camera
                        clip = support.in_camera(Tf)
                    elif support is not None:  # the support plane in this frame's camera, moved into the part by the margin
                        clip = (Tf[:3, :3].T @ support[0], float(support[0] @ Tf[:3, 3]) + support[1] - float(support_margin_mm))
                    res = pipe.process(frame, pose=T @ Tf, clip_plane=clip)
                    gfb = (getattr(res, "extra", None) or {}).get("gpu_fallback")
                    if gfb:  # a GPU stage failed on this frame (the frame is fused with its own matcher's factor)
                        de = getattr(pipe, "depth_estimator", None)
                        fallbacks += [{"scan": name, **fb} for fb in
                                      fallback_entries(gfb, int(frame.index), getattr(de, "gpu_failed_device", None))]
                    if full_at is None and getattr(pipe.markerless, "pool_full", False):
                        full_at = {"scan": name, "frame": int(frame.index)}
                    if progress is not None:
                        progress(k, n)
                    k += 1
            finally:
                frames.close()
            per_scan.append({"name": name, "recording": str(reader.path), "frames": len(positions), "T": T.tolist(),
                             "support_left_out": support is not None, "calibration": ci,
                             "image_size": list(pipe.image_size), "table": tables[len(per_scan)]})
            log.info("merged %s: %d frames", name, len(positions))
        V, F, N, S, L = pipe.mesh_with_sigma(with_looks=True)  # L: each vertex's independent looks
        n_faces_raw = int(len(F))
        # what the frames left of each moved scan's table (module docstring): its rule on the merged mesh
        gone = np.zeros(len(F), dtype=bool)
        for (name, _, _, _, T, support, _), entry in zip(plan, tables):
            if isinstance(support, SupportMap):
                left = _support.table_left(V, F, support, T=T)
                gone[left.faces] = True
                entry["left_in_mesh"] = left.to_dict()
        if gone.any():
            from ohs.mesh.topology import compact  # noqa: PLC0415

            V, F, idx = compact(V, F[~gone])
            N = N[idx] if N is not None and len(N) else N
            S = S[idx] if S is not None and len(S) else S
            L = L[idx] if L is not None and len(L) else L
        n_faces_table = int(gone.sum())
        maps = [(support, T) for (_, _, _, _, T, support, _) in plan if isinstance(support, SupportMap)]
        cleaning = None
        if p.clean_min_component > 0 and len(F):
            if maps:  # a thin part lying flat is a piece of its own: not cleaned away for being small
                V, F, idx, cleaning = clean_merged(V, F, maps, float(p.clean_min_component))
            else:
                from ohs.track.export import clean_mesh  # noqa: PLC0415

                # what it takes, counted as clean_merged counts it (the same pieces: tests/test_merge_table.py), so
                # that a merge of a part that was not moved says it too (UX_PLAN R7); the mesh is clean_mesh's, as before
                cleaning = clean_merged(V, F, [], float(p.clean_min_component))[3]
                V, F, idx = clean_mesh(V, F, min_component_fraction=float(p.clean_min_component), return_index=True)
            N = N[idx] if N is not None and len(N) else N
            S = S[idx] if S is not None and len(S) else S
            L = L[idx] if L is not None and len(L) else L
        for (_, _, _, _, T, support, _), entry in zip(plan, tables):
            if isinstance(support, SupportMap):  # what of its thin parts is in the result
                settle_thin_parts(entry, support, V, F, T)
        if S is not None:
            S = np.minimum(S, np.float32(SIGMA_NONE_MM)).astype(np.float32)
        from ohs.track.export import save_mesh, save_points_ply  # noqa: PLC0415

        files: dict[str, str] = {}
        for ext in export:
            path = out / f"mesh.{ext}"
            save_mesh(path, V, F, N if ext != "stl" and N is not None and len(N) == len(V) else None,
                      quality=S if ext == "ply" and S is not None and len(S) == len(V) else None,
                      looks=L if ext == "ply" and L is not None and len(L) == len(V) else None)
            files[f"mesh_{ext}"] = str(path)
        cloud = pipe.laser_cloud()
        laser_pts = cloud.points(float(p.laser_voxel_mm))
        on_table = np.zeros(len(laser_pts), dtype=bool)
        for (_, _, _, _, T, support, _), entry in zip(plan, tables):
            if isinstance(support, SupportMap):
                hit = _support.laser_points_on_table(laser_pts, support, T=T) & ~on_table
                entry["laser_points_removed"] = int(hit.sum())
                on_table |= hit
        if on_table.any():
            laser_pts = laser_pts[~on_table]
        save_points_ply(out / "laser_points.ply", laser_pts)
        files["laser_points"] = str(out / "laser_points.ply")
        planes |= set(pipe.planes)
        warnings: list[str] = []
        for s, entry in zip(per_scan, tables):
            if entry is None:
                continue
            if entry["rule"] == "map":
                entry["summary"] = _map_summary(entry)
            tag = f"\"{s['name']}\""
            warnings += [w if tag in w else f"{tag}: {w}" for w in entry["warnings"]]
        report: dict[str, Any] = {
            "merged": True, "out_dir": str(out), "scans": per_scan, "frames": {"fused": n},
            "image_size": per_scan[0]["image_size"], "calibrations": len(cals),
            "mesh": {"n_vertices": int(len(V)), "n_faces": int(len(F)), "n_faces_before_cleaning": n_faces_raw,
                     "voxel_mm": float(pipe.fusion_params.voxel_mm), "empty": len(F) == 0,
                     "bbox_mm": [V.min(0).tolist(), V.max(0).tolist()] if len(V) else None,
                     "reconstruction": pipe.recon_status, "sigma_mm": sigma_stats(S, V, F)},
            "lasers": {"planes": sorted(planes), "n_points_exported": int(len(laser_pts)),
                       "voxel_mm": float(p.laser_voxel_mm)},
            "files": files,
            "warnings": warnings,
        }
        if any(isinstance(pl[5], SupportMap) for pl in plan):
            report["mesh"]["n_faces_table_removed"] = n_faces_table
            report["lasers"]["n_points_table_removed"] = int(on_table.sum())
        # the mesh model's pool (ENGINE_PLAN R2-5), as process_recording says it: None for a model that keeps no
        # such status (Open3D's)
        pool_of = getattr(pipe.markerless, "capacity_status", None)
        report["mesh_capacity"] = None
        if pool_of is not None:
            cap = {**pool_of(), "first_refused_frame": None if full_at is None else full_at["frame"],
                   "first_refused_scan": None if full_at is None else full_at["scan"]}
            report["mesh_capacity"] = cap
            if cap["full"]:
                from ohs.app.process import pool_full_warning  # noqa: PLC0415
                where = None if full_at is None else f"{full_at['frame']} of \"{full_at['scan']}\""
                warnings.append(pool_full_warning({**cap, "first_refused_frame": where}))
                log.warning("%s", warnings[-1])
        if fallbacks:  # only then: a merge without one saves what it always did
            from ohs.app.process import matcher_fallback_warning  # noqa: PLC0415

            report["compute"] = {"fallbacks": fallbacks}
            for fb in fallbacks:
                if fb["stage"] == "depth":
                    warnings.append(matcher_fallback_warning({**fb, "frame": f"{fb['frame']} of \"{fb['scan']}\""}))
                    log.warning("%s", warnings[-1])
        if cleaning is not None:  # what the small-piece cleaning did: nothing goes unsaid, moved part or not (R7)
            report["mesh"]["cleaning"] = {**cleaning, "min_component_fraction": float(p.clean_min_component),
                                          "summary": _cleaning_summary(cleaning, float(p.clean_min_component), bool(maps))}
        files["report"] = str(out / "report.json")
        report["wall_time_s"] = time.perf_counter() - wall0
        report = _jsonable(report)
        (out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    finally:
        pipe.close()
    log.info("merged %d scans, %d frames, in %.1f s -> %s", len(plan), n, report["wall_time_s"], out)
    return report
