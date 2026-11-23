"""The overall size of a large part: a 1.3 m plain panel scanned in passes (ENGINE_PLAN X8, step 1).

    python tools/large_part_study.py budget WORK [--frames 20]        # render 20 frames of seed 0, time them
    python tools/large_part_study.py render WORK SEED [--frames N]    # one recording -> WORK/recs/s<SEED>[-n<N>]
    python tools/large_part_study.py process WORK REC {tracked,true}  # what Make mesh runs -> WORK/runs/<REC>/<poses>
    python tools/large_part_study.py judge WORK REC                   # the numbers -> WORK/runs/<REC>/judge.json
    python tools/large_part_study.py table WORK                       # the table of what is judged

(run from ``software/``). A measurement, not an engine change: nothing in the engine or its defaults is touched; the
process step wraps two engine classes in its own process only to count what they do (below). Every step is one
process; render and process are heavy (the graphics card, every processor core): run them one at a time
(``VALIDATION.md``, X8). The order per seed: ``render``, ``process tracked``, ``process true``, ``judge``; ``table``.

**The scene** (:func:`panel_scene`, built from the simulator's existing pieces): a plain painted panel, one surface
(``HeightField`` "panel"), 1300 x 400 mm, gently doubly curved and convex up (radii 6 m along, 2 m across), lying on a
2 x 2 m dark table on its four corners (1 mm above it); two Ø10 holes on the centre line 30 mm from each end, 14 mm
above the table; paint and table as the car panel's. Nothing along its length to hold on to but its edges.

**The scan** (:func:`sweep`): ``SweepTrajectory`` as the door's in ``VALIDATION.md`` "Large and detailed parts"
(distance 240-290 mm, elevation 60-75°, the same hand noise), here 3 passes along the panel, out, back, out, the aim
travelling 1200 mm, the passes 130 mm apart, 56.5 s at about 100 mm/s, rendered as the scorecard renders (1280 x
800, realistic) at :data:`FPS`.

**process**: ``tools/scorecard.py``'s ``app_chain`` (Follow the camera, Straighten, Refine with lasers, the server's
own parameters, Standard quality, 1 mm mesh detail), tracked, or fused at the true poses (the ceiling, and each
method's own bias). Counted on the way (the job's process only): every move of the graphics card's rolling 768 mm
tracking cube (``GpuTsdfVolume.follow``), the mesh model's brick pool (``ConfidenceTsdf._slots_for``), the warnings
logged, the memory (a run past :data:`MAX_WS_GB` is stopped).

**judge** (the module's metrics; each mesh after the Default recipe, as the scorecard): (1) end to end by the Hole
tool, clicked as the scorecard clicks the car panel's hole, each end's clicks placed by that end alone; (2) end to
end from the mesh's two short edges (their middles, read against a local fit of the panel); (3) end to end from the
camera path: placed on the true path by the frames at one end (in position; the turn is the whole path's), how far
the frames at the other end put the panel;
(4) the bow, from the mesh placed by its two ends (along the normal) and from its long edges (sideways), and from the
path; (5) the slide between passes at revisit points found from the truth, before and after Straighten; the path's
error, the mesh against the true panel, time, memory, the app's warnings and Straighten's report, the cube.

**Fails** (fixed before any result, ``VALIDATION.md`` X8): over :data:`LIMIT_PCT` % end to end on any seed, by the Hole
tool where it answers or by the edges against the same seed's true-pose reading.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from ohs import geometry as G  # noqa: E402
from tools import scorecard as SC  # noqa: E402

# --------------------------------------------------------------------------- the set-up (fixed beforehand)
HALF_L, HALF_W = 650.0, 200.0  # the panel: 1300 x 400 mm
RX, RY = 6000.0, 2000.0  # its radii along and across (convex up)
CORNER_Z = 1.0  # its corners rest this far above the table
H0 = CORNER_Z + HALF_L ** 2 / (2 * RX) + HALF_W ** 2 / (2 * RY)  # its top above the table
HOLE_X, HOLE_R = 620.0, 5.0  # Ø10 holes on the centre line, 30 mm from each end
TABLE_HALF = 1000.0
PASSES, TRAVEL, SPACING = 3, 1200.0, 130.0
AIM_Z = 30.0
DURATION = 56.5  # s: one pass 18.85 s, about 100 mm/s at its middle
FPS = 10.0
SEEDS = (0, 1, 2)
LIMIT_PCT = 0.05
MAX_WS_GB = 15.0  # a process past this is stopped (this PC has 19.8 GB)
END_X = 450.0  # a frame or vertex "at an end": |x| beyond this; "in the middle": |x| below MID_X
MID_X = 150.0
CLICK_DR = 1.5  # mm outside the hole's rim, as the scorecard clicks the car panel's hole
AHEAD_MM = 265.0


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def write_json(path: Path, obj: Any) -> None:
    SC.write_json(path, SC._plain(obj))


# --------------------------------------------------------------------------- scene and scan
def height(x, y):
    """The panel's top in its own frame (z up from H0) and its slopes."""
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    return -x * x / (2 * RX) - y * y / (2 * RY), -x / RX, -y / RY


def panel_scene(seed: int = 0):
    """The scene (module docstring)."""
    from ohs.sim.scene import HeightField, NoiseAlbedo, Scene, _table

    T = G.make_se3(np.eye(3), [0.0, 0.0, H0])
    zmin = float(height(HALF_L, HALF_W)[0])
    panel = HeightField(T, height, (-HALF_L, HALF_L, -HALF_W, HALF_W), (zmin, 0.0), [(-HOLE_X, 0.0, HOLE_R), (HOLE_X, 0.0, HOLE_R)],
                        NoiseAlbedo(mean=0.72, contrast=0.015, scale_mm=4.0, seed=seed), name="panel")
    table = _table(0.0, TABLE_HALF, NoiseAlbedo(mean=0.3, contrast=0.08, scale_mm=5.0, seed=seed + 1), name="table")
    return Scene([table, panel], name="panel1300", target=(0.0, 0.0, AIM_Z), radius=700.0, meta={"panel_T_wo": T.tolist()})


def sweep(seed: int):
    from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration
    from ohs.sim.trajectory import SweepTrajectory

    calib = nominal_calibration(SC.IMAGE_SIZE, realistic=True)
    return SweepTrajectory(target=np.array([0.0, 0.0, AIM_Z]), t0=100.0 + NOMINAL_TIME_OFFSET, duration=DURATION, passes=PASSES,
                           length_mm=TRAVEL, spacing_mm=SPACING, heading_deg=0.0, distance=(240.0, 290.0), elevation_deg=(60.0, 75.0),
                           roll_deg=8.0, aim_jitter_mm=4.0, tremor_mm=0.08, tremor_deg=0.03, seed=seed, baseline=calib.stereo.baseline)


def n_frames(fps: float = FPS) -> int:
    return int(round(DURATION * fps))


def pass_of(i: int, fps: float) -> int:
    return min(int((i / fps) // (DURATION / PASSES)), PASSES - 1)


def spec_for(rec: Path) -> SC.SceneSpec:
    m = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
    return SC.SceneSpec(rec.name, "panel1300", "large_panel", int(m["seed"]), int(m["n_frames"]), {"maker": "large"})


def render(work: Path, seed: int, frames: int | None = None, fps: float = FPS) -> Path:
    from ohs.sim.datasets import make_scan_recording

    n = frames or n_frames(fps)
    path = work / "recs" / (f"s{seed}" if frames is None else f"s{seed}-n{frames}")
    if (path / "done").is_file():
        return path
    shutil.rmtree(path, ignore_errors=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    t = time.perf_counter()
    make_scan_recording(path, scene=panel_scene(seed), n_frames=n, fps=fps, trajectory=sweep(seed), overwrite=True,
                        image_size=SC.IMAGE_SIZE, realistic=True, depth_every=0, seed=seed)
    secs = time.perf_counter() - t
    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    write_json(path / "done", {"frames": n, "fps": fps, "render_s": round(secs, 1), "bytes": size})
    log(f"{path.name}: {n} frames in {secs:.0f} s ({secs / n:.2f} s a frame), {size / 2 ** 30:.2f} GB")
    return path


def budget(work: Path, frames: int) -> None:
    path = render(work, 0, frames)
    d = json.loads((path / "done").read_text(encoding="utf-8"))
    per, size = d["render_s"] / frames, d["bytes"] / frames
    for fps in (FPS, 8.0, 6.0):
        n = n_frames(fps)
        log(f"at {fps:g} fps: {n} frames, about {per * n / 60:.0f} min and {size * n / 2 ** 30:.1f} GB a seed")


# --------------------------------------------------------------------------- process (what Make mesh runs)
def _instrument() -> dict:
    """Count, in this process only, what the tracking cube and the mesh model do, and the warnings logged."""
    import ohs.track.recon_tsdf as RT
    import ohs.track.volume_gpu as VG

    st: dict[str, Any] = {"cube": {}, "pool": {}, "warnings": {}}
    follow0 = VG.GpuTsdfVolume.follow

    def follow(self, T_wc):
        moved = follow0(self, T_wc)
        c = st["cube"].setdefault(str(id(self)), {"calls": 0, "moves": 0, "edge_mm": float(self.size_mm), "moves_at": []})
        c["calls"] += 1
        if moved:
            c["moves"] += 1
            c["moves_at"].append([c["calls"], [round(float(v), 1) for v in np.asarray(T_wc)[:3, 3]]])
        return moved

    VG.GpuTsdfVolume.follow = follow
    slots0 = RT.ConfidenceTsdf._slots_for

    def slots(self, keys):
        out = slots0(self, keys)
        p = st["pool"].setdefault(str(id(self)), {})
        p.update(n_bricks=int(self.n_bricks), capacity=int(self.capacity), max_bricks=int(self.max_bricks),
                 voxel_mm=float(self.voxel_mm))
        return out

    RT.ConfidenceTsdf._slots_for = slots

    class Grab(logging.Handler):
        def emit(self, r: logging.LogRecord) -> None:
            if r.levelno >= logging.WARNING:
                k = f"{r.name}: {r.getMessage()}"[:240]
                st["warnings"][k] = st["warnings"].get(k, 0) + 1

    logging.getLogger().addHandler(Grab())
    return st


def _watch_memory(stop: threading.Event, out: dict) -> None:
    from tools.memory_peaks import process_memory

    while not stop.wait(1.0):
        m = process_memory(os.getpid()) or {}
        ws = float(m.get("ws") or 0.0) / 1e9
        out["max_ws_gb"] = max(out.get("max_ws_gb", 0.0), ws)
        if ws > MAX_WS_GB:
            print(f"STOPPED: working set {ws:.1f} GB > {MAX_WS_GB} GB", flush=True)
            os._exit(95)


def process(work: Path, rec_name: str, poses: str, params: dict | None = None, tag: str = "") -> Path:
    """``params`` (``scorecard.parse_params``: e.g. ``fusion.gpu_volume_voxels=768``) is for a diagnostic run only, in
    its own folder ``<poses>-<tag>``; the study's runs use none."""
    from ohs.io.recording import RecordingReader
    from tools.memory_peaks import Sampler, summarize

    rec = work / "recs" / rec_name
    out = work / "runs" / rec_name / (poses + (f"-{tag}" if tag else ""))
    if (out / "done.json").is_file():
        return out
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    spec = spec_for(rec)
    st = _instrument()
    given = dict(RecordingReader(rec).ground_truth_poses()) if poses == "true" else None
    mem: dict[str, Any] = {}
    stop = threading.Event()
    th = threading.Thread(target=_watch_memory, args=(stop, mem), daemon=True)
    th.start()
    t0 = time.perf_counter()
    with Sampler(os.getpid(), None, every=1.0, seconds=6 * 3600.0) as smp:
        rep, info = SC.app_chain(rec, out, spec, dict(params or {}), given)
    wall = time.perf_counter() - t0
    stop.set()
    peaks = summarize(smp.rows).get("", {}) if smp.rows else {}
    stg = out / "stages"
    for name in ("trajectory.csv", "report.json"):  # the tracked path before Straighten, and its report
        if (stg / "track" / name).is_file():
            shutil.copy2(stg / "track" / name, out / f"track_{name}")
    if (stg / "straighten.json").is_file():
        shutil.copy2(stg / "straighten.json", out / "straighten.json")
    shutil.rmtree(stg, ignore_errors=True)
    tr = json.loads((out / "track_report.json").read_text(encoding="utf-8")) if (out / "track_report.json").is_file() else {}
    done = {"poses": poses, "params": params or {}, "make_mesh_s": round(wall, 1), "chain": info,
            "peak_ws_gb": round(float(peaks["peak_ws"]) / 1e9, 2) if peaks.get("peak_ws") else None,
            "peak_gpu_shared_gb": round(float(peaks["gpu_shared"]) / 1e9, 2) if peaks.get("gpu_shared") else None,
            "watch_max_ws_gb": round(mem.get("max_ws_gb", 0.0), 2),
            "frames": tr.get("frames") or rep.get("frames"), "ground_truth": tr.get("ground_truth"),
            "warnings": tr.get("warnings"), "path_check": tr.get("path_check"),
            "final_warnings": rep.get("warnings"), "cube": st["cube"], "pool": st["pool"], "logged": st["warnings"]}
    write_json(out / "done.json", done)
    log(f"{rec_name} {poses}: {wall:.0f} s, peak {done['peak_ws_gb']} GB; cube moves "
        f"{[c['moves'] for c in st['cube'].values()]}; straighten {json.dumps(SC._plain(info.get('straighten')))[:300]}")
    return out


# --------------------------------------------------------------------------- the truth
def panel_of(sc):
    return sc.primitives[sc.object_id("panel")]


def to_local(sc, P: np.ndarray) -> np.ndarray:
    p = panel_of(sc)
    return (np.asarray(P, np.float64).reshape(-1, 3) - p.t) @ p.R


def to_world(sc, L: np.ndarray) -> np.ndarray:
    return G.transform_points(panel_of(sc).T_wo, np.asarray(L, np.float64).reshape(-1, 3))


def surface_local(x, y) -> np.ndarray:
    x, y = np.atleast_1d(np.asarray(x, np.float64)), np.atleast_1d(np.asarray(y, np.float64))
    return np.column_stack([x, y, height(x, y)[0]])


def frame_at(sc, P: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """World unit vectors at panel points ``P``: along the length, across, the normal (up)."""
    L = to_local(sc, P)
    _, hx, hy = height(L[:, 0], L[:, 1])
    R = panel_of(sc).R
    tx = np.column_stack([np.ones_like(hx), np.zeros_like(hx), hx])
    tx /= np.linalg.norm(tx, axis=1, keepdims=True)
    n = np.column_stack([-hx, -hy, np.ones_like(hx)])
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    ty = np.cross(n, tx)
    return tx @ R.T, ty @ R.T, n @ R.T


def true_hole(sc, k: int) -> dict:
    """Hole ``k``: the circle an exact fit gives on the true edge in the plane of the true ring round it (as
    ``scorecard.panel_hole_world``)."""
    from ohs.measure.fit import fit_circle, fit_plane

    hx0, hy0, r0 = panel_of(sc).holes[k]
    th = np.linspace(0, 2 * math.pi, 1440, endpoint=False)
    E = to_world(sc, surface_local(hx0 + r0 * np.cos(th), hy0 + r0 * np.sin(th)))
    rr, tt = np.meshgrid(np.linspace(r0, r0 + 3.0, 13), th[::4])
    Rg = to_world(sc, surface_local(hx0 + rr.ravel() * np.cos(tt.ravel()), hy0 + rr.ravel() * np.sin(tt.ravel())))
    pl = fit_plane(Rg)
    n = pl.normal if pl.normal @ panel_of(sc).R[:, 2] >= 0 else -pl.normal
    c = fit_circle(E, normal=n)
    return {"centre": np.asarray(c.center), "diameter": 2.0 * float(c.radius_mm), "local": (hx0, hy0, r0)}


def truth_of(sc):
    from tools.preset_study import _panel_mesh

    V, F = _panel_mesh(sc, step=1.0)
    return SC.MeshTruth(V, F, 0.0)


# --------------------------------------------------------------------------- the path
def path_rows(traj: Path) -> dict[int, np.ndarray]:
    return dict(SC._traj(traj))


def view_points(sc, gt: dict[int, np.ndarray], idx: list[int]) -> tuple[list[int], np.ndarray]:
    """The true panel point under each frame's image centre (frames whose centre ray misses the panel are left out)."""
    O = np.array([gt[i][:3, 3] for i in idx])
    D = np.array([gt[i][:3, 2] for i in idx])
    hit = sc.cast(O, D, surface=False)
    ok = np.isfinite(hit.t) & (hit.obj == sc.object_id("panel"))
    return [i for i, k in zip(idx, ok) if k], O[ok] + hit.t[ok, None] * D[ok]


def fit_world(trk: dict, gt: dict, idx: list[int]) -> np.ndarray:
    """The tracked world in the true one that best fits frames ``idx`` (each camera centre and a point ahead)."""
    P, Q = [], []
    for i in idx:
        for z in (0.0, AHEAD_MM):
            P.append(trk[i][:3, 3] + z * trk[i][:3, 2])
            Q.append(gt[i][:3, 3] + z * gt[i][:3, 2])
    return SC._kabsch(np.asarray(P), np.asarray(Q))


def fit_shift(trk: dict, gt: dict, idx: list[int], R: np.ndarray) -> np.ndarray:
    """As :func:`fit_world` over frames ``idx`` but turned by ``R`` (the whole path's fit) and fitted only in position: a
    patch of frames at one end of the panel fixes the turn badly (a few tenths of a degree read as tens of mm a metre
    away), while the gyro holds the turn along the whole path."""
    P, Q = [], []
    for i in idx:
        for z in (0.0, AHEAD_MM):
            P.append(trk[i][:3, 3] + z * trk[i][:3, 2])
            Q.append(gt[i][:3, 3] + z * gt[i][:3, 2])
    return G.make_se3(R, np.mean(np.asarray(Q) - np.asarray(P) @ R.T, axis=0))


def where_tracked(trk: dict, gt: dict, idx: list[int], P: np.ndarray) -> np.ndarray:
    """Where each frame's tracked pose puts true point ``P[k]`` (the tracked world)."""
    return np.array([G.transform_points(trk[i] @ G.invert_se3(gt[i]), P[k:k + 1])[0] for k, i in enumerate(idx)])


def path_metrics(sc, trk: dict, gt: dict, fps: float) -> dict:
    """End to end, bow and the path's error from the camera path (metrics 3 and 4b)."""
    idx = sorted(i for i in trk if i in gt)
    idx, P = view_points(sc, gt, idx)
    Q = where_tracked(trk, gt, idx, P)
    x = to_local(sc, P)[:, 0]
    tx, ty, n = frame_at(sc, P)
    passes = np.array([pass_of(i, fps) for i in idx])
    A, B, M = x < -END_X, x > END_X, np.abs(x) < MID_X
    Tall = fit_world(trk, gt, idx)
    Rall = Tall[:3, :3]

    def offsets(T: np.ndarray, sel: np.ndarray) -> dict:
        e = G.transform_points(T, Q[sel]) - P[sel]
        return {"n": int(sel.sum()), "along": float(np.median(np.einsum("ij,ij->i", e, tx[sel]))),
                "across": float(np.median(np.einsum("ij,ij->i", e, ty[sel]))),
                "normal": float(np.median(np.einsum("ij,ij->i", e, n[sel])))}

    def anchored(sel_a: np.ndarray, sel_b: np.ndarray) -> dict | None:
        if sel_a.sum() < 3 or sel_b.sum() < 3:
            return None
        ia, ib = [idx[k] for k in np.flatnonzero(sel_a)], [idx[k] for k in np.flatnonzero(sel_b)]
        b_at_a, a_at_b = offsets(fit_shift(trk, gt, ia, Rall), sel_b), offsets(fit_shift(trk, gt, ib, Rall), sel_a)
        err = 0.5 * (b_at_a["along"] - a_at_b["along"])
        span = float(np.median(x[sel_b]) - np.median(x[sel_a]))  # how far apart the two ends' views are
        return {"B_when_A_placed": b_at_a, "A_when_B_placed": a_at_b, "length_err_mm": err, "span_mm": span,
                "length_err_pct": 100.0 * err / span}

    out: dict[str, Any] = {"frames": len(idx), "end_to_end": anchored(A, B)}
    out["per_pass"] = {str(k): anchored(A & (passes == k), B & (passes == k)) for k in range(PASSES)}
    ends = [idx[k] for k in np.flatnonzero(A | B)]
    out["bow"] = offsets(fit_world(trk, gt, ends), M) if M.sum() >= 3 and len(ends) >= 3 else None
    C = np.array([trk[i][:3, 3] for i in idx])
    Ct = np.array([gt[i][:3, 3] for i in idx])
    d_fit = np.linalg.norm(G.transform_points(Tall, C) - Ct, axis=1)
    T_first = gt[idx[0]] @ G.invert_se3(trk[idx[0]])
    d_first = np.linalg.norm(G.transform_points(T_first, C) - Ct, axis=1)
    out["error_fitted"] = {"median": float(np.median(d_fit)), "max": float(d_fit.max())}
    out["error_first_frame"] = {"median": float(np.median(d_first)), "max": float(d_first.max()), "last": float(d_first[-1])}
    # the along-length error of each view point with the whole path fitted, by pass and position: where it grows
    e = G.transform_points(Tall, Q) - P
    al = np.einsum("ij,ij->i", e, tx)
    out["along_by_x"] = {str(k): [[float(lo), float(np.median(al[(passes == k) & (x >= lo) & (x < lo + 200)]))]
                                  for lo in range(-600, 600, 200) if ((passes == k) & (x >= lo) & (x < lo + 200)).sum() >= 3]
                         for k in range(PASSES)}
    return out


def revisits(sc, trk: dict, gt: dict, K: np.ndarray, size: tuple[int, int], fps: float) -> dict:
    """Metric 5: where two passes saw the same true point, how far their tracked poses put it apart."""
    xs, ys = np.arange(-600.0, 600.1, 50.0), np.arange(-150.0, 150.1, 50.0)
    X, Y = np.meshgrid(xs, ys)
    P = to_world(sc, surface_local(X.ravel(), Y.ravel()))
    _, _, n = frame_at(sc, P)
    w, h = size
    idx = sorted(i for i in trk if i in gt)
    best = np.full((len(P), PASSES), -1, np.int64)
    score = np.full((len(P), PASSES), np.inf)
    for i in idx:
        Tc = G.invert_se3(gt[i])
        pc = G.transform_points(Tc, P)
        z = pc[:, 2]
        u = K[0, 0] * pc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]
        v = K[1, 1] * pc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
        facing = np.einsum("ij,ij->i", gt[i][:3, 3] - P, n) > 0
        ok = (z > 150) & (z < 450) & (u > 20) & (u < w - 20) & (v > 20) & (v < h - 20) & facing
        s = np.hypot(u - K[0, 2], v - K[1, 2])
        k = pass_of(i, fps)
        better = ok & (s < score[:, k])
        score[better, k] = s[better]
        best[better, k] = i
    R = fit_world(trk, gt, idx)[:3, :3]
    tx, ty, _ = frame_at(sc, P)
    xl = to_local(sc, P)[:, 0]
    out: dict[str, Any] = {}
    for a, b in ((0, 1), (1, 2), (0, 2)):
        sel = np.flatnonzero((best[:, a] >= 0) & (best[:, b] >= 0))
        if not len(sel):
            continue
        qa = np.array([G.transform_points(trk[best[k, a]] @ G.invert_se3(gt[best[k, a]]), P[k:k + 1])[0] for k in sel])
        qb = np.array([G.transform_points(trk[best[k, b]] @ G.invert_se3(gt[best[k, b]]), P[k:k + 1])[0] for k in sel])
        d = (qa - qb) @ R.T
        comp = {"along": np.einsum("ij,ij->i", d, tx[sel]), "across": np.einsum("ij,ij->i", d, ty[sel]),
                "gap": np.einsum("ij,ij->i", d, n[sel])}
        r: dict[str, Any] = {"points": int(len(sel))}
        for c, v in comp.items():
            r[f"{c}_rms"] = float(np.sqrt(np.mean(v ** 2)))
            r[f"{c}_max"] = float(np.max(np.abs(v)))
        r["along_by_x"] = [[float(lo), float(np.mean(comp["along"][(xl[sel] >= lo) & (xl[sel] < lo + 400)]))]
                           for lo in (-600, -200, 200) if ((xl[sel] >= lo) & (xl[sel] < lo + 400)).any()]
        out[f"{a + 1}-{b + 1}"] = r
    return out


# --------------------------------------------------------------------------- the mesh
def _quad_fit(L: np.ndarray) -> np.ndarray | None:
    """``z = c0 + c1 x + c2 y + c3 x^2 + c4 y^2 + c5 x y`` fitted robustly to local points ``L``."""
    if len(L) < 30:
        return None
    x, y, z = L[:, 0], L[:, 1], L[:, 2]
    A = np.column_stack([np.ones_like(x), x, y, x * x, y * y, x * y])
    keep = np.ones(len(L), bool)
    c = None
    for _ in range(3):
        c, *_ = np.linalg.lstsq(A[keep], z[keep], rcond=None)
        r = np.abs(A @ c - z)
        mad = float(np.median(r[keep])) + 1e-6
        keep = r < max(4.0 * 1.4826 * mad, 0.15)
    return c


def _quad(c: np.ndarray, x, y):
    return c[0] + c[1] * x + c[2] * y + c[3] * x * x + c[4] * y * y + c[5] * x * y


def end_edge(L: np.ndarray, s: float) -> np.ndarray | None:
    """The middle of the short end edge on side ``s`` (+1 / -1) in local coordinates of a placed mesh (metric 2)."""
    sx = s * L[:, 0]
    near = np.abs(L[:, 2] - height(L[:, 0], L[:, 1])[0]) < 3.0
    reg = near & (sx > HALF_L - 150) & (sx < HALF_L - 20) & (np.abs(L[:, 1]) < 110)
    c = _quad_fit(L[reg])
    if c is None:
        return None
    cand = (sx > HALF_L - 60) & (sx < HALF_L + 15) & (np.abs(L[:, 1]) < 100) & (np.abs(L[:, 2] - _quad(c, L[:, 0], L[:, 1])) < 0.5)
    xe = []
    for y0 in np.arange(-100.0, 100.0, 10.0):
        sel = cand & (L[:, 1] >= y0) & (L[:, 1] < y0 + 10)
        if sel.sum() >= 3:
            xe.append(float(np.max(sx[sel])))
    if len(xe) < 5:
        return None
    x = s * float(np.median(xe))
    return np.array([x, 0.0, float(_quad(c, x, 0.0))])


def long_edges(L: np.ndarray) -> dict:
    """The long edges' sideways positions per 25 mm station (local y), for the sideways bow."""
    out: dict[str, list] = {}
    near = np.abs(L[:, 2] - height(L[:, 0], L[:, 1])[0]) < 5.0
    for s, name in ((-1.0, "near"), (1.0, "far")):
        sy = s * L[:, 1]
        rows = []
        for x0 in np.arange(-575.0, 575.1, 25.0):
            reg = near & (np.abs(L[:, 0] - x0) < 60) & (sy > HALF_W - 120) & (sy < HALF_W - 20)
            c = _quad_fit(L[reg])
            if c is None:
                continue
            cand = (np.abs(L[:, 0] - x0) < 12.5) & (sy > HALF_W - 20) & (sy < HALF_W + 15) & (np.abs(L[:, 2] - _quad(c, L[:, 0], L[:, 1])) < 0.5)
            if cand.sum() >= 3:
                rows.append([float(x0), s * float(np.max(sy[cand]))])
        out[name] = rows
    return out


def sideways_bow(edges: dict) -> dict | None:
    r = {}
    for name, rows in edges.items():
        a = np.asarray(rows)
        if not len(a):
            return None
        mid, end = a[np.abs(a[:, 0]) < MID_X, 1], a[np.abs(a[:, 0]) > END_X, 1]
        if not len(mid) or not len(end):
            return None
        r[name] = float(np.mean(mid) - np.mean(end))
        r[f"{name}_width_reading"] = float(np.mean(a[:, 1]))
    r["bow"] = 0.5 * (r["near"] + r["far"])
    return r


def hole_tool(sc, truth, V2, F2, S2, Fl2, T: np.ndarray) -> dict:
    """Metric 1: the Hole tool at each end, its clicks placed by that end's region alone; centres in the mesh's frame."""
    out: dict[str, Any] = {}
    cen = []
    for k, name in ((0, "A"), (1, "B")):
        h = true_hole(sc, k)
        hx, hy, r = h["local"]
        L = to_local(sc, G.transform_points(T, V2))
        sel = (np.abs(L[:, 0] - hx) < 150) & (np.abs(L[:, 1]) < 150)
        Te, pinfo = SC.place_part(V2[sel], T, truth)
        ck = SC.Clicker(G.transform_points(Te, V2), F2, S2, Fl2)
        clicks = to_world(sc, surface_local([hx + (r + CLICK_DR) * math.cos(math.radians(a)) for a in (0, 120, 240)],
                                            [hy + (r + CLICK_DR) * math.sin(math.radians(a)) for a in (0, 120, 240)]))
        cs, ds, refused = [], [], []
        for p in clicks:
            fit, err, dist = ck.fit(p, "hole")
            if fit is None:
                refused.append(err)
                continue
            cs.append(G.transform_points(G.invert_se3(Te), np.asarray(fit.fit.center).reshape(1, 3))[0])
            ds.append(2.0 * float(fit.fit.radius_mm))
        c = np.median(np.asarray(cs), axis=0) if cs else None
        cen.append(c)
        out[name] = {"answered": len(cs), "refused": refused, "d": float(np.median(ds)) if ds else None,
                     "d_err": float(np.median(ds)) - h["diameter"] if ds else None, "true_d": h["diameter"],
                     "end_placement_moved_mm": pinfo.get("moved_mm")}
    true_dist = float(np.linalg.norm(true_hole(sc, 0)["centre"] - true_hole(sc, 1)["centre"]))
    out["true_mm"] = true_dist
    if cen[0] is not None and cen[1] is not None:
        d = float(np.linalg.norm(cen[0] - cen[1]))
        out.update(mm=d, err_mm=d - true_dist, err_pct=100.0 * (d - true_dist) / true_dist)
    return out


def mesh_metrics(sc, truth, mesh: Path, T0: np.ndarray) -> dict:
    from ohs.mesh.order import canonical
    from ohs.track.export import load_ply
    from tools import preset_study as ps

    d = load_ply(mesh)
    c = canonical(np.asarray(d["vertices"], np.float64), np.asarray(d["faces"], np.int64))
    V, F, S = c.V, c.F, c.vertex_values(d["quality"])
    V2, F2, S2, Fl2, _ = ps.apply_ops(V, F, S, ps.RECIPES["general"])
    T, pinfo = SC.place_part(V2, T0, truth)
    out: dict[str, Any] = {"vertices": int(len(V2)), "faces": int(len(F2)), "placement": pinfo}
    out.update(SC.part_scores(G.transform_points(T, V2), truth))
    t = time.perf_counter()
    out["holes"] = hole_tool(sc, truth, V2, F2, S2, Fl2, T)
    out["holes_s"] = round(time.perf_counter() - t, 1)
    # metric 2: the short edges' middles, put back into the mesh's own frame
    L = to_local(sc, G.transform_points(T, V2))
    ea, eb = end_edge(L, -1.0), end_edge(L, 1.0)
    true_len = float(np.linalg.norm(surface_local(HALF_L, 0.0) - surface_local(-HALF_L, 0.0)))
    e: dict[str, Any] = {"true_mm": true_len}
    if ea is not None and eb is not None:
        back = G.invert_se3(T)
        pa, pb = (G.transform_points(back, to_world(sc, q))[0] for q in (ea, eb))
        ln = float(np.linalg.norm(pa - pb))
        e.update(mm=ln, err_mm=ln - true_len, err_pct=100.0 * (ln - true_len) / true_len, local_A=ea.tolist(), local_B=eb.tolist())
    out["edges"] = e
    # metric 4a: the bow, the mesh placed by its two ends
    ends = (np.abs(L[:, 0]) > END_X) & (np.abs(L[:, 1]) < HALF_W - 10)
    Tn, ninfo = SC.place_part(V2[ends], T, truth)
    Vn = G.transform_points(Tn, V2)
    dist, part, sp = truth.nearest(Vn)
    Ln = to_local(sc, Vn)
    mid = part & (np.abs(Ln[:, 0]) < MID_X) & (np.abs(Ln[:, 1]) < 150) & (dist < 5.0)
    endm = part & (np.abs(Ln[:, 0]) > END_X) & (np.abs(Ln[:, 1]) < 150) & (dist < 5.0)
    edges = long_edges(Ln)
    out["bow"] = {"normal_mid": float(np.median(sp[mid])) if mid.any() else None,
                  "normal_ends": float(np.median(sp[endm])) if endm.any() else None,
                  "sideways": sideways_bow(edges), "placement": ninfo, "long_edges": edges}
    return out


# --------------------------------------------------------------------------- judge and table
def judge(work: Path, rec_name: str) -> dict:
    from ohs.io.recording import RecordingReader

    rec = work / "recs" / rec_name
    meta = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
    seed, fps = int(meta["seed"]), float(meta["fps"])
    sc = panel_scene(seed)
    truth = truth_of(sc)
    reader = RecordingReader(rec)
    gt = reader.ground_truth_poses()
    stc = reader.calibration().stereo
    K, size = np.asarray(stc.K_rect, np.float64), tuple(stc.image_size)
    res: dict[str, Any] = {"rec": rec_name, "seed": seed, "fps": fps, "frames": len(gt),
                           "true_hole_mm": float(np.linalg.norm(true_hole(sc, 0)["centre"] - true_hole(sc, 1)["centre"]))}
    runs = sorted(p.name for p in (work / "runs" / rec_name).iterdir() if (p / "done.json").is_file())
    for folder in sorted(runs, key=lambda n: (n not in ("tracked", "true"), n)):
        run = work / "runs" / rec_name / folder
        poses = "true" if folder.startswith("true") else "tracked"
        t = time.perf_counter()
        done = json.loads((run / "done.json").read_text(encoding="utf-8"))
        r: dict[str, Any] = {k: done.get(k) for k in ("params", "make_mesh_s", "peak_ws_gb", "peak_gpu_shared_gb", "watch_max_ws_gb", "frames",
                                                       "ground_truth", "warnings", "path_check", "final_warnings", "logged")}
        r["cube"] = {k: {"calls": c["calls"], "moves": c["moves"], "edge_mm": c["edge_mm"]} for k, c in done.get("cube", {}).items()}
        r["pool"] = done.get("pool")
        ch = done.get("chain") or {}
        r["stage_s"] = ch.get("stage_s")
        r["straighten"] = ch.get("straighten")
        r["refine"] = ch.get("refine")
        T0 = np.eye(4)
        if poses == "tracked":
            trk0, trk = path_rows(run / "track_trajectory.csv"), path_rows(run / "trajectory.csv")
            T0 = fit_world(trk, gt, sorted(i for i in trk if i in gt))
            r["path_track"] = path_metrics(sc, trk0, gt, fps)
            r["path_final"] = path_metrics(sc, trk, gt, fps)
            r["revisits_track"] = revisits(sc, trk0, gt, K, size, fps)
            r["revisits_final"] = revisits(sc, trk, gt, K, size, fps)
            sj = run / "straighten.json"
            if sj.is_file():
                s = json.loads(sj.read_text(encoding="utf-8"))
                r["straighten_report"] = {k: s.get(k) for k in ("verdict", "reason", "loops_accepted", "loop_candidates", "keyframes",
                                                                 "correction_mm", "correction_deg", "residual_mm", "ground_truth")}
                r["straighten_report"]["crossings"] = {k: (s.get("laser_curves") or {}).get(k) for k in ("verdict", "warning", "changed",
                                                                                                      "crossings", "moved_mm")}
        r["mesh"] = mesh_metrics(sc, truth, run / "mesh.ply", T0)
        r["judge_s"] = round(time.perf_counter() - t, 1)
        res[folder] = r
        log(f"{rec_name} {folder}: judged in {r['judge_s']} s")
    tr, tp = res.get("tracked"), res.get("true")
    if tr and tp:
        a, b = tr["mesh"]["edges"].get("mm"), tp["mesh"]["edges"].get("mm")
        if a is not None and b is not None:
            res["edges_vs_true_poses"] = {"err_mm": a - b, "err_pct": 100.0 * (a - b) / tr["mesh"]["edges"]["true_mm"]}
    write_json(work / "runs" / rec_name / "judge.json", res)
    return res


def _f(x: Any, nd: int = 3) -> str:
    return "n/m" if x is None else f"{x:.{nd}f}"


def verdict(res: dict) -> tuple[str, list[str]]:
    """Fails (module docstring) when the Hole tool or the edges against the true-pose reading are over the limit."""
    why = []
    h = ((res.get("tracked") or {}).get("mesh") or {}).get("holes") or {}
    if h.get("err_pct") is not None and abs(h["err_pct"]) > LIMIT_PCT:
        why.append(f"Hole tool {h['err_pct']:+.3f} %")
    e = res.get("edges_vs_true_poses") or {}
    if e.get("err_pct") is not None and abs(e["err_pct"]) > LIMIT_PCT:
        why.append(f"edges {e['err_pct']:+.3f} %")
    if h.get("err_pct") is None and e.get("err_pct") is None:
        return "not measured", why
    return ("FAILS" if why else "passes"), why


def table(work: Path) -> None:
    for f in sorted((work / "runs").glob("*/judge.json")):
        j = json.loads(f.read_text(encoding="utf-8"))
        v, why = verdict(j)
        print(f"\n== {j['rec']} (seed {j['seed']}, {j['frames']} frames at {j['fps']:g} fps): {v} {'; '.join(why)}")
        for name, r in j.items():
            if not (isinstance(r, dict) and "mesh" in r):
                continue
            poses = "true" if name.startswith("true") else "tracked"
            print(f"  [{name}] {r.get('params') or ''}")
            m = r["mesh"]
            h, e, b = m["holes"], m["edges"], m["bow"]
            print(f"  {poses:7s} holes: {_f(h.get('err_mm'))} mm ({_f(h.get('err_pct'))} %), answered {h['A']['answered']}/{h['B']['answered']}, "
                  f"d_err {_f(h['A']['d_err'], 2)}/{_f(h['B']['d_err'], 2)}; refused {h['A']['refused'][:1]} {h['B']['refused'][:1]}")
            print(f"          edges: {_f(e.get('err_mm'))} mm ({_f(e.get('err_pct'))} %); bow normal {_f(b['normal_mid'])} mm "
                  f"(ends {_f(b['normal_ends'])}), sideways {_f((b.get('sideways') or {}).get('bow'))} mm")
            print(f"          mesh median {_f(m.get('part_med'))} mm, over 0.3 mm {_f(m.get('part_over03'))}; Make mesh {r['make_mesh_s']} s; "
                  f"peak {r['peak_ws_gb']} GB; cube {r['cube']}")
            if poses == "tracked":
                for k in ("path_track", "path_final"):
                    p = r[k]
                    ee = p.get("end_to_end") or {}
                    print(f"          {k}: end to end {_f(ee.get('length_err_mm'))} mm, bow {p.get('bow')}, err fitted {p['error_fitted']}, "
                          f"first {p['error_first_frame']}")
                    print(f"            per pass {[_f((v or {}).get('length_err_mm')) for v in p['per_pass'].values()]}")
                for k in ("revisits_track", "revisits_final"):
                    print(f"          {k}: " + "; ".join(f"{pp} along {_f(v['along_rms'], 2)}/{_f(v['along_max'], 2)} across "
                                                       f"{_f(v['across_rms'], 2)} gap {_f(v['gap_rms'], 2)}" for pp, v in r[k].items()))
                s = r.get("straighten_report") or {}
                print(f"          straighten: {s.get('verdict')} loops {s.get('loops_accepted')} corr {s.get('correction_mm')}; "
                      f"crossings {s.get('crossings')}")
                print(f"          warnings {r.get('warnings')} path_check {(r.get('path_check') or {}).get('state')}")
        if j.get("edges_vs_true_poses"):
            print(f"  edges tracked against true poses: {j['edges_vs_true_poses']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=("budget", "render", "process", "judge", "table"))
    ap.add_argument("work", type=Path)
    ap.add_argument("args", nargs="*")
    ap.add_argument("--frames", type=int, default=None)
    ap.add_argument("--fps", type=float, default=FPS)
    ap.add_argument("--set-param", action="append", default=None, help="process, a diagnostic run only: SECTION.FIELD=VALUE")
    ap.add_argument("--tag", default="", help="process with --set-param: the run's folder is <poses>-<tag>")
    a = ap.parse_args(argv)
    if a.set_param and not a.tag:
        ap.error("--set-param needs --tag (a diagnostic run never takes the study's own folder)")
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.what == "budget":
        budget(a.work, a.frames or 20)
    elif a.what == "render":
        render(a.work, int(a.args[0]), a.frames, a.fps)
    elif a.what == "process":
        process(a.work, a.args[0], a.args[1], SC.parse_params(a.set_param), a.tag)
    elif a.what == "judge":
        judge(a.work, a.args[0])
    else:
        table(a.work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
