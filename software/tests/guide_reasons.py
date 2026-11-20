"""Simulated scans whose weak areas have a known reason, for ``tests/test_guide_loop.py`` and the
study behind ``VALIDATION.md`` ("Why an area is weak, and where to scan it from").

The scene is the thin plate on its table (``ohs.sim.scene.thin_plate``, 1 mm, 60 x 40 mm): flat,
matte surface whose sigma is ruled by how it was seen, not by edges. :func:`case_scan`:

* ``"steep"``: 40 frames, the scanner 24-30 degrees above the table: the table and the plate seen
  only 60-66 degrees off their normal;
* ``"control"``: the same from 62-72 degrees above (18-28 degrees off the normal);
* ``"far"``: from 62-72 degrees above as well, but 385-415 mm away (the working distance is 254);
* ``"few"``: the control scan's 60 frames round the plate, merged with a single short pass (12
  frames) over the table 450 mm away, where the first scan's frames reach no surface within
  their depth range: that patch of table is seen in one pass only.

All at the true poses in the simulator's world. Returns the Check answer's areas by reason (their
area, the largest one's evidence) and the sigma of the surface round each target, so the test can
set its tolerance between what a well-seen and a badly seen surface give.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

FAR_TARGET = np.array([0.0, 450.0, 0.0])


def _traj(target, t0, n, seed, baseline, distance, elevation):
    from ohs.sim.trajectory import HandheldTrajectory

    return HandheldTrajectory(target=np.asarray(target, dtype=np.float64), t0=t0, duration=n / 30.0, seed=seed,
                              distance=distance, elevation_deg=elevation, sweep_deg_s=20.0, baseline=baseline,
                              azimuth0_deg=-90.0)


def case_scan(tmp: Path, case: str, size: tuple[int, int], tol: float | None = None, use_looks: bool = True,
              recon: str = "auto") -> dict:
    """One case (module docstring): the Check answer at ``tol`` (when given) with every reason the
    data allow (``use_looks`` False leaves the looks out), and the sigma round the targets."""
    from guide_loop import scan
    from ohs.align import ScanInput, merge_scans
    from ohs.app.pipeline import PipelineParams
    from ohs.app.process import ProcessParams
    from ohs.mesh.check import ScanViews, weak_areas
    from ohs.sim.nominal import nominal_calibration
    from ohs.track.export import load_ply
    from ohs.track.posegraph import read_trajectory
    from ohs.vision.depth import DepthParams

    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    cal = nominal_calibration(size, realistic=True)
    t0 = 100.0 + float(cal.imu.time_offset)
    B = cal.stereo.baseline
    params = ProcessParams(pipeline=PipelineParams(recon=recon), export=("ply",))
    target = np.zeros(3)
    tag = f"{size[0]}_{recon}"
    if case == "steep":
        runs = [("steep", _traj(target, t0, 40, 11, B, (240.0, 265.0), (24.0, 30.0)), 40, 11)]
    elif case == "control":
        runs = [("control", _traj(target, t0, 40, 12, B, (240.0, 265.0), (62.0, 72.0)), 40, 12)]
    elif case == "far":
        runs = [("far", _traj(target, t0, 40, 13, B, (385.0, 415.0), (62.0, 72.0)), 40, 13)]
    elif case == "few":
        runs = [("few_a", _traj(target, t0, 60, 14, B, (240.0, 265.0), (62.0, 72.0)), 60, 14),
                ("few_b", _traj(FAR_TARGET, t0, 12, 15, B, (240.0, 265.0), (62.0, 72.0)), 12, 15)]
    else:
        raise ValueError(case)
    outs = []
    for name, traj, n, seed in runs:
        rec, out, rep = scan(tmp, f"{name}_{tag}", traj, n, size, seed, params, scene="thin_plate")
        outs.append((rec, out, rep))
    if len(outs) == 1:
        mesh = outs[0][1] / "mesh.ply"
        voxel = outs[0][2]["mesh"]["voxel_mm"]
    else:
        merged = tmp / f"merged_{case}_{tag}"
        rep = merge_scans([ScanInput(r, o / "trajectory.csv", name=o.name) for r, o, _ in outs], merged, params)
        mesh = merged / "mesh.ply"
        voxel = rep["mesh"]["voxel_mm"]
    d = load_ply(mesh)
    V, F, N, S, L = d["vertices"], d["faces"], d["normals"], d["quality"], d["looks"]
    dp = DepthParams()
    views = [ScanViews.from_calibration(read_trajectory(o / "trajectory.csv"), cal, (dp.min_depth_mm, dp.max_depth_mm))
             for _, o, _ in outs]
    res: dict = {"case": case, "size": list(size), "n_vertices": int(len(V)), "has_looks": L is not None,
                 "has_sigma": S is not None}
    for nm, c in (("target", target), ("far_target", FAR_TARGET)):
        m = np.linalg.norm(V[:, :2] - c[:2], axis=1) < 60.0
        if S is not None and m.any():
            res[f"sigma_{nm}"] = np.percentile(S[m], [10, 50, 90]).round(4).tolist()
        if L is not None and m.any():
            res[f"looks_{nm}"] = np.percentile(L[m], [10, 50, 90]).round(1).tolist()
    if tol is None or S is None:
        return res
    r = weak_areas(V, F, S, tolerance_mm=tol, normals=N, looks=L if use_looks else None, views=views, voxel_mm=voxel)
    by: dict[str, float] = {}
    near: dict[str, float] = {}
    near_any: dict[str, float] = {}  # by every reason that holds, not only the first
    for a in r["areas"]:
        by[a["reason"]] = by.get(a["reason"], 0.0) + a["area_mm2"]
        c = FAR_TARGET if case == "few" else target
        if np.linalg.norm(np.asarray(a["center"])[:2] - c[:2]) < 120.0:
            near[a["reason"]] = near.get(a["reason"], 0.0) + a["area_mm2"]
            near["_all"] = near.get("_all", 0.0) + a["area_mm2"]
            for x in a["reasons"]:
                near_any[x] = near_any.get(x, 0.0) + a["area_mm2"]
    res.update({"tol": tol, "share_ok": r["share_ok"], "n_areas": r["n_areas"], "area_by_reason": by,
                "near_target_by_reason": near, "near_target_holds": near_any, "reasons": r["reasons"],
                "largest": None if not r["areas"] else {k: r["areas"][0][k] for k in
                                                        ("reason", "reasons", "area_mm2", "center", "evidence")}})
    return res
