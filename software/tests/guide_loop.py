"""The whole loop of FEATURE_PLAN N1 on the simulator, for ``tests/test_guide_loop.py`` and the study
behind ``VALIDATION.md`` ("Why an area is weak, and where to scan it from").

:func:`run_loop`: the bracket scanned from one side only (a 60-frame hand-held sweep, fused at its
true poses, in the simulator's world); its weak areas with their reasons; ``K`` short passes (20
frames, two thirds of a second: a hand-held sweep of about 15 degrees round the pose) either from
the poses :func:`ohs.guide.plan_views` plans for the part's areas (those mostly off the table that
``ohs.mesh.support.find_support`` finds on the mesh) (``"guided"``), or from ``K`` random directions above the table aimed at the part's centre at the
same working distance (``"random"``); every scan merged into one model (``ohs.align.merge_scans``,
all in the simulator's world, so no alignment error enters). What it measures, before and after:

* ``first_surface_ok``: of the part's true surface that the first scan has (the true points within 1
  mm of its mesh), the share that the mesh has within 1 mm and that meets the tolerance there (2
  sigma at most the tolerance): the surface the Check answer's areas are on, measured well enough;
* ``truth_ok``: the same share of all of the part's true surface (the surface of the simulated part,
  apart from the table, that can be seen from above the table: :func:`truth_points`), seen or not
  by the first scan: what new surface a pass finds counts here too;
* ``check_ok`` / ``part_check_ok``: the Check answer's share that passes, of the whole mesh and of
  the part above the table (its own surface, which grows as more is seen).

Recordings go through the tests' cache (``tests/recordings.py``).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

N_FIRST = 60
N_PASS = 20
FPS = 30.0
PART_Z_MM = 1.0  # what lies this far above the table is the part


def truth_points(scene: Any, spacing_mm: float = 1.0, n_dirs: int = 120) -> np.ndarray:
    """Points of the scene's part (every primitive but the table) that can be seen from above the
    table: parallel rays from ``n_dirs`` directions (elevation 5-89 degrees, evenly spread), a grid
    ``spacing_mm`` apart over the part, first hits kept, then one point per ``spacing_mm`` cell."""
    table = scene.object_id("table")
    c = np.asarray(scene.target, dtype=np.float64)
    r = float(scene.radius)
    pts = []
    k = np.arange(n_dirs) + 0.5
    z = np.sin(np.radians(5.0)) + (1.0 - np.sin(np.radians(5.0))) * k / n_dirs  # even in solid angle over the band
    az = math.pi * (1.0 + math.sqrt(5.0)) * k
    D = np.stack([np.sqrt(1 - z * z) * np.cos(az), np.sqrt(1 - z * z) * np.sin(az), z], axis=1)
    g = np.arange(-r, r + 1e-9, spacing_mm)
    gu, gv = np.meshgrid(g, g)
    for d in D:
        a = np.cross(d, [0.0, 0.0, 1.0]) if abs(d[2]) < 0.99 else np.array([1.0, 0.0, 0.0])
        a /= np.linalg.norm(a)
        b = np.cross(d, a)
        O = c + 3.0 * r * d + gu.reshape(-1, 1) * a + gv.reshape(-1, 1) * b
        hit = scene.cast(O, np.broadcast_to(-d, O.shape).copy(), surface=False)
        m = np.isfinite(hit.t) & (hit.obj != table) & (hit.obj >= 0)
        pts.append(O[m] - hit.t[m, None] * d)
    P = np.concatenate(pts)
    _, first = np.unique(np.floor(P / spacing_mm).astype(np.int64), axis=0, return_index=True)
    return P[np.sort(first)]


def measure(mesh_ply: Path, truth: np.ndarray, tol: float, first: np.ndarray | None = None) -> dict:
    """The numbers of the module docstring for one mesh; ``first``: which truth points the first
    scan's mesh has (``"covered_mask"`` of its own measure), for ``first_surface_ok``."""
    from scipy.spatial import cKDTree

    from ohs.mesh.check import weak_areas
    from ohs.track.export import load_ply

    d = load_ply(mesh_ply)
    V, F, S = d["vertices"], d["faces"], d["quality"]
    r = weak_areas(V, F, S, tolerance_mm=tol)
    part = V[F].mean(axis=1)[:, 2] > PART_Z_MM
    rp = weak_areas(V, F[part], S, tolerance_mm=tol)
    dist, j = cKDTree(V).query(truth, distance_upper_bound=1.0)
    near = np.isfinite(dist)
    good = np.zeros(len(truth), dtype=bool)
    good[near] = 2.0 * S[j[near]] <= tol
    by_tol = {}
    for t in (0.3, 0.5, 1.0):
        g = np.zeros(len(truth), dtype=bool)
        g[near] = 2.0 * S[j[near]] <= t
        by_tol[str(t)] = float(g.mean())
    first = near if first is None else first
    return {"truth_ok": float(good.mean()), "truth_ok_by_tol": by_tol, "truth_covered": float(near.mean()),
            "first_surface_ok": float(good[first].mean()) if first.any() else None, "covered_mask": near,
            "check_ok": r["share_ok"],
            "part_check_ok": rp["share_ok"], "n_vertices": int(len(V)), "part_area_mm2": rp["area_mm2"]}


def _pass_trajectory(target, direction, distance, seed: int, t0: float, baseline: float):
    """A short hand-held pass round ``direction`` (unit, from the target to the scanner): a sweep of
    the simulator's hand-held motion (22 degrees a second, +-5 degrees of elevation, +-10 mm)."""
    from ohs.sim.trajectory import HandheldTrajectory

    d = np.asarray(direction, dtype=np.float64)
    el = math.degrees(math.asin(np.clip(d[2], -1.0, 1.0)))
    el = min(max(el, 8.0), 78.0)  # the motion model's range (it cannot look straight down)
    az = math.degrees(math.atan2(d[1], d[0]))
    return HandheldTrajectory(target=np.asarray(target, dtype=np.float64), t0=t0, duration=N_PASS / FPS, seed=seed,
                              distance=(distance - 10.0, distance + 10.0), elevation_deg=(el - 5.0, el + 5.0),
                              azimuth0_deg=az, sweep_deg_s=22.0, baseline=baseline, aim_jitter_mm=3.0)


def scan(tmp: Path, name: str, traj, n: int, size, seed: int, params, scene: str = "bracket") -> tuple[Path, Path, dict]:
    """A simulated recording of ``scene`` along ``traj`` (``n`` frames), processed at its true poses
    (the simulator's world): ``(recording, output folder, report)``."""
    from ohs.app.process import process_recording
    from ohs.io.recording import RecordingReader
    from recordings import scan_recording

    rec = scan_recording(tmp / f"rec_{name}", scene=scene, n_frames=n, image_size=size, realistic=True,
                         depth_every=0, seed=seed, trajectory=traj, overwrite=True)
    gt = RecordingReader(rec).ground_truth_poses()
    out = tmp / f"out_{name}"
    rep = process_recording(rec, out, params=params, poses=gt)
    return rec, out, rep


def run_loop(tmp: Path, size: tuple[int, int], seed: int, method: str, K: int = 3, tol: float = 0.5,
             azimuth: float = 20.0, recon: str = "auto", use_looks: bool = True) -> dict:
    """The loop (module docstring) for one seed and one ``method``; returns the numbers. ``recon``:
    the reconstruction model (``"confidence"`` on the CPU path: the numpy twin, the only model there
    with a sigma); ``use_looks`` False: the reasons without the count of looks."""
    from ohs.align import ScanInput, merge_scans
    from ohs.app.pipeline import PipelineParams
    from ohs.app.process import ProcessParams
    from ohs.guide import plan_views, suggest_views
    from ohs.mesh.check import ScanViews, weak_areas
    from ohs.sim.nominal import nominal_calibration
    from ohs.sim.scene import make_scene
    from ohs.sim.trajectory import HandheldTrajectory
    from ohs.track.export import load_ply

    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    sc = make_scene("bracket", 0)
    cal = nominal_calibration(size, realistic=True)
    t0 = 100.0 + float(cal.imu.time_offset)
    params = ProcessParams(pipeline=PipelineParams(recon=recon), export=("ply",))
    first = HandheldTrajectory(target=sc.target, t0=t0, duration=N_FIRST / FPS, seed=seed, baseline=cal.stereo.baseline,
                               azimuth0_deg=azimuth, **dict(sc.meta.get("trajectory", {})))
    recA, outA, repA = scan(tmp, f"first_{seed}_{recon}", first, N_FIRST, size, seed, params)
    truth_file = tmp / "truth.npy"
    if truth_file.is_file():
        truth = np.load(truth_file)
    else:
        truth = truth_points(sc)
        np.save(truth_file, truth)
    d = load_ply(outA / "mesh.ply")
    V, F, N, S, L = d["vertices"], d["faces"], d["normals"], d["quality"], d["looks"]
    from ohs.track.posegraph import read_trajectory

    dp = repA["params"]["depth"]  # the depth range the scan measured
    views = ScanViews.from_calibration(read_trajectory(outA / "trajectory.csv"), cal, (dp["min_depth_mm"], dp["max_depth_mm"]))
    check = weak_areas(V, F, S, tolerance_mm=tol, normals=N, looks=L if use_looks else None, views=views,
                       voxel_mm=repA["mesh"]["voxel_mm"])
    sugg = suggest_views(V, F, check, views, normals=N)
    rng = np.random.default_rng(1000 + seed)
    passes = []
    chosen = []
    # the part's areas: those mostly off the table the engine finds on the mesh (ohs.mesh.support)
    from ohs.mesh.support import find_support

    table = np.zeros(len(F), dtype=bool)
    table[find_support(V, F, normals=N, sigma=S).faces] = True
    if method == "guided":  # the engine's plan for the part's areas (ohs.guide.plan_views)
        part = [i for i, a in enumerate(check["areas"]) if table[a["faces"]].mean() < 0.5]
        for p in plan_views(V, F, check, views, K, normals=N, select=part, suggestions=sugg):
            a = check["areas"][p["area"]]
            chosen.append({"reason": a["reason"], "area_mm2": a["area_mm2"], "share_seen": p["share_seen"],
                           "border_seen": p["border_seen"], "tilt_deg": p["pose"]["tilt_deg"], "kind": p["kind"],
                           "center": a["center"]})
            passes.append((np.asarray(p["pose"]["target_mm"]), -np.asarray(p["pose"]["look_dir"])))
    else:
        for _ in range(K):
            el = math.radians(rng.uniform(15.0, 75.0))
            az = rng.uniform(-math.pi, math.pi)
            passes.append((np.asarray(sc.target), np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az),
                                                            math.sin(el)])))
            chosen.append({"elevation_deg": math.degrees(el), "azimuth_deg": math.degrees(az)})
    scans = [ScanInput(recA, outA / "trajectory.csv", name="first")]
    for k, (target, direction) in enumerate(passes):
        traj = _pass_trajectory(target, direction, views.working_mm, 7000 + 10 * seed + k, t0, cal.stereo.baseline)
        rec, out, _ = scan(tmp, f"{method}_{seed}_{k}_{recon}", traj, N_PASS, size, 7000 + 10 * seed + k, params)
        scans.append(ScanInput(rec, out / "trajectory.csv", name=f"{method} {k}"))
    merged = tmp / f"merged_{method}_{seed}_{recon}"
    merge_scans(scans, merged, params)
    before = measure(outA / "mesh.ply", truth, tol)
    after = measure(merged / "mesh.ply", truth, tol, first=before["covered_mask"])
    before.pop("covered_mask")
    after.pop("covered_mask")
    reasons: dict[str, int] = {}
    part_reasons: dict[str, int] = {}
    for a in check["areas"]:
        reasons[a["reason"]] = reasons.get(a["reason"], 0) + 1
        if table[a["faces"]].mean() < 0.5:
            part_reasons[a["reason"]] = part_reasons.get(a["reason"], 0) + 1
    return {"seed": seed, "method": method, "K": K, "tol": tol, "size": list(size), "before": before, "after": after,
            "gain_truth_ok": after["truth_ok"] - before["truth_ok"],
            "gain_first_surface_ok": after["first_surface_ok"] - before["first_surface_ok"], "n_areas": check["n_areas"], "reasons": reasons,
            "part_reasons": part_reasons,
            "poses_ok": int(sum(s["ok"] for s in sugg)), "passes": chosen, "working_mm": views.working_mm,
            "n_truth": int(len(truth)), "has_looks": L is not None, "used_looks": use_looks and L is not None,
            "recon": repA["mesh"]["reconstruction"]}
