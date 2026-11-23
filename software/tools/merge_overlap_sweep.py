"""How much must two scans share for their alignment to be safe to merge? (the merge warning after X7)

    python tools/merge_overlap_sweep.py singles                      (the scans to make, one command each)
    python tools/merge_overlap_sweep.py align WORK [--scans DIR ...] [--only PAIR ...] [--starts shape points]
    python tools/merge_overlap_sweep.py place WORK [--scans DIR ...]
    python tools/merge_overlap_sweep.py table WORK
    python tools/merge_overlap_sweep.py after X7WORK                 (the after-merge check on saved merges)

(run from ``software/``.) X7 (``tools/merge_accuracy_study.py``, VALIDATION.md "The merge on today's engine")
found merges of two scans that share little coming out far worse than either scan, with the alignment's
verdict saying nothing. This tool measures, over many pairs, the alignment's numbers against how far its pose
is from the truth, to place the limit of :data:`ohs.align.pairwise.AlignParams.caution_overlap`.

* The scans (:data:`SCANS`) are 90-frame stretches of the scorecard's orbit recordings, made as Make mesh makes
  them by ``merge_accuracy_study.py single WORK SCAN tracked`` (graphics card, about 35 s each, one at a time).
  ``singles`` prints those commands. ``--scans`` names further folders that already hold such scans (read only:
  X7's ``work``).
* The pairs (:func:`pairs`): every two scans of one scene from different seeds (independent scans of a part
  that was not moved). Each is aligned as the app does (``align_pair``, same setup, the second scan into the
  first's world) from the shape and from points "picked" on both (``merge_accuracy_study.picked_start``: four
  true matches, each 1.5 mm off). Processor only.
* Per alignment: the result (``as_dict``), its pose error against the true motion through the first cameras and
  through all frames (``fitted_world``: the "pose off" used, a tracked scan's first frame can be millimetres off),
  and signals beside the verdict's: ``reach_mm`` (95th percentile of each part's points' distance to its
  nearest matched point, the larger of the two scans), ``core_mm`` (RMS radius of the matched points) and
  ``lever`` (their ratio).
* ``place``: a second truth for each row, ``error_placed``: the pose against the motion between the two meshes as
  each fits the true part best (``scorecard.place_part``). A tracked mesh need not sit where its camera path's fitted
  world puts it; neither truth is good for a scan whose own mesh is bent (the sheet's), which only a merge at the
  pose and at the true motion settles (``merge_accuracy_study.py pair ... --only a b2`` and ``judge``).
* ``table``: one row per alignment, and for each signal whether a threshold separates the accepted alignments
  whose pose is more than :data:`OFF_MM` off from the others (against either truth), with its margin.
* ``after``: a check after the merge that needs no truth (:func:`after_check`: each scan's own part against the
  merged mesh) on saved merges. Measured 2026-11-23 on X7's merges and the sweep's: it does not tell the ruined
  merges from the sound ones (a merge at a pose that is off follows each scan where only that scan saw).

Measured 2026-11-23: VALIDATION.md "Two scans that share little"; the limit is
:data:`ohs.align.pairwise.CAUTION_OVERLAP`.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from ohs import geometry as G  # noqa: E402
from tools import merge_accuracy_study as MS  # noqa: E402

#: scene: scans ``name@start`` with their centre azimuth in degrees (circular mean over the stretch)
SCANS: dict[str, dict[str, float]] = {
    "bracket": {"bracket240-s0@0": 272, "bracket240-s0@130": 129, "bracket240-s1@20": 86, "bracket240-s2@140": 9,
                "bracket240-s3@0": 73, "bracket240-s4@0": 275},
    "sheet": {"sheet-s0@110": 96, "sheet-s0@30": 322, "sheet-s1@100": 311, "sheet-s1@40": 53, "sheet-s1@140": 246,
              "sheet-s2@50": 219, "sheet-s2@0": 137},
    "holes": {"holes-low-s0@0": 282, "holes-low-s1@10": 79, "holes-low-s1@60": 341, "holes-low-s2@20": 186,
              "holes-low-s2@60": 267},
    "panel": {"panel200-s4@0": 293, "panel200-s0@70": 32},
}
OFF_MM = 0.6  # a pose this far off the truth (median over the source scan) is "off" (NOTES: 1.5x the worst sound X7 pair)
SIGNALS = ("overlap", "pick", "rmse_mm", "fit_mm", "reach_mm", "lever")


def seed_of(key: str) -> str:
    return key.split("@")[0]


def angle(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def pairs() -> list[tuple[str, str, str, float]]:
    """``(scene, scan A, scan B, degrees apart)``: every two scans of a scene from different seeds."""
    out = []
    for scene, scans in SCANS.items():
        for a, b in itertools.combinations(scans, 2):
            if seed_of(a) != seed_of(b):
                out.append((scene, a, b, angle(scans[a], scans[b])))
    return out


def pair_name(a: str, b: str) -> str:
    return f"{a.replace('@', '_')}__{b.replace('@', '_')}"


def find_scan(key: str, dirs: list[Path]) -> Path | None:
    for d in dirs:
        p = MS.scan_dir(d, key, "tracked")
        if (p / "done.json").is_file():
            return p
    return None


def signals(src, dst, T: np.ndarray, tol: float) -> dict:
    """``reach_mm``, ``core_mm``, ``lever`` of two scan clouds at ``T`` (module docstring)."""
    from scipy.spatial import cKDTree

    from ohs.track import posegraph as pg

    a, b = src.part, dst.part
    i, j = pg._match(a.fine.pts, a.fine.nrm, b.fine, T, tol)
    i2, _ = pg._match(b.fine.pts, b.fine.nrm, a.fine, G.invert_se3(T), tol)
    if len(i) < 10 or len(i2) < 10:
        return {"reach_mm": None, "core_mm": None, "lever": None}
    reach = []
    for P, idx in ((a.fine.pts, i), (b.fine.pts, i2)):
        d, _ = cKDTree(P[idx]).query(P, k=1)
        reach.append(float(np.percentile(d, 95)))
    M = a.fine.pts[i]
    core = float(np.sqrt(np.mean(np.sum((M - M.mean(axis=0)) ** 2, axis=1))))
    r = max(reach)
    return {"reach_mm": r, "core_mm": core, "lever": r / core if core > 0 else None}


def run_align(work: Path, recs: Path, dirs: list[Path], only: list[str] | None, starts: list[str]) -> None:
    from ohs.align import AlignParams, align_pair, scan_cloud_from_mesh
    from ohs.track.export import load_mesh

    out_dir = work / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    clouds: dict[str, object] = {}

    def cloud(key: str, path: Path):
        if key not in clouds:
            clouds[key] = scan_cloud_from_mesh(path / "mesh.ply")
        return clouds[key]

    p = AlignParams()
    for scene, ka, kb, deg in pairs():
        name = pair_name(ka, kb)
        if only and name not in only and scene not in only:
            continue
        A, B = find_scan(ka, dirs), find_scan(kb, dirs)
        if A is None or B is None:
            MS.log(f"{name}: scan missing ({ka}: {A}, {kb}: {B}), skipped")
            continue
        ra, rb = recs / seed_of(ka), recs / seed_of(kb)
        T_first = G.invert_se3(MS.first_camera(ra, A / "trajectory.csv")) @ MS.first_camera(rb, B / "trajectory.csv")
        T_fit = G.invert_se3(MS.fitted_world(ra, A / "trajectory.csv")) @ MS.fitted_world(rb, B / "trajectory.csv")
        Vb, _, _ = load_mesh(B / "mesh.ply")
        for how in starts:
            f = out_dir / f"{name}__{how}.json"
            if f.is_file():
                continue
            dst, src = cloud(ka, A), cloud(kb, B)
            t0 = time.perf_counter()
            init = picks_rms = None
            if how == "points":
                init, picks_rms, _ = MS.picked_start(A, B, T_fit)
            res = align_pair(src, dst, p, init=init, same_setup=True)
            s = time.perf_counter() - t0
            row = {"scene": scene, "a": ka, "b": kb, "deg": deg, "start": how, "accepted": bool(res.accepted),
                   "result": res.as_dict(), "pick": res.poses[0]["pick"] if res.poses else None,
                   "error_first": MS.pose_error(res.T, T_first, Vb[::7]), "error": MS.pose_error(res.T, T_fit, Vb[::7]),
                   "init_error": None if init is None else MS.pose_error(init, T_fit, Vb[::7]), "picks_rms_mm": picks_rms,
                   **signals(src, dst, res.T, p.match_mm), "s": round(s, 1)}
            MS.write_json(f, row)
            MS.log(f"{name} {how} ({deg:.0f} deg): accepted {res.accepted} ({res.doubt}), overlap {res.overlap:.3f}, "
                   f"rmse {res.rmse_mm:.3f}, off {row['error']['median_mm']:.2f} mm, {s:.0f} s")


def placed_world(work: Path, recs: Path, key: str, scan: Path) -> np.ndarray:
    """The scan's world in the simulator's as its mesh's part fits the true part best (``scorecard.place_part`` from
    the fitted world, as the judge places a mesh; cached in ``WORK/placed``): where the mesh must go for the part to
    be right, whatever its camera path did."""
    from ohs.track.export import load_mesh
    from tools import scorecard as SC

    f = work / "placed" / f"{key.replace('@', '_')}.json"
    if f.is_file():
        return np.asarray(json.loads(f.read_text(encoding="utf-8"))["T"])
    spec, _, _ = MS.scan_spec(key)
    sc = SC.scene_of(spec)
    truth = SC.truth_for(spec, sc)
    V, _, _ = load_mesh(scan / "mesh.ply")
    T, info = SC.place_part(V, MS.fitted_world(recs / seed_of(key), scan / "trajectory.csv"), truth)
    f.parent.mkdir(parents=True, exist_ok=True)
    MS.write_json(f, {"T": T.tolist(), "info": info})
    return T


def run_place(work: Path, recs: Path, dirs: list[Path]) -> None:
    """Adds ``error_placed`` to every row: the pose against the motion between the two meshes as each fits the true
    part best (:func:`placed_world`). The fitted worlds of tracked frames are not where a drifted mesh fits."""
    from ohs.track.export import load_mesh

    for f in sorted((work / "sweep").glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        if "error_placed" in r:
            continue
        A, B = find_scan(r["a"], dirs), find_scan(r["b"], dirs)
        T_pl = G.invert_se3(placed_world(work, recs, r["a"], A)) @ placed_world(work, recs, r["b"], B)
        Vb, _, _ = load_mesh(B / "mesh.ply")
        r["error_placed"] = MS.pose_error(np.asarray(r["result"]["T"]), T_pl, Vb[::7])
        MS.write_json(f, r)
        MS.log(f"{f.stem}: off the fitted worlds {r['error']['median_mm']:.2f} mm, off the placed meshes "
               f"{r['error_placed']['median_mm']:.2f} mm")


def load_rows(work: Path) -> list[dict]:
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted((work / "sweep").glob("*.json"))]


def is_off(r: dict, truth: str) -> bool:
    return r[truth]["median_mm"] > OFF_MM


def separation(rows: list[dict], key: str, truth: str = "error") -> dict:
    """Whether ``key`` separates the accepted rows that are off (against ``truth``: ``error``, the fitted worlds, or
    ``error_placed``) from the others: the best threshold, which side is off, how many it gets wrong, and the
    margin (the gap between the closest values across it; negative when they overlap)."""
    vals = [(float(r[key] if key in r else r["result"][key]), is_off(r, truth)) for r in rows
            if r["accepted"] and truth in r and (r.get(key) if key in r else r["result"].get(key)) is not None]
    if not vals or all(o for _, o in vals) or not any(o for _, o in vals):
        return {"key": key, "n": len(vals), "note": "only one kind"}
    best = None
    xs = sorted({v for v, _ in vals})
    cuts = [(x + y) / 2 for x, y in zip(xs, xs[1:])]
    for c in cuts:
        for off_below in (True, False):
            wrong = sum(1 for v, o in vals if (v < c) != o) if off_below else sum(1 for v, o in vals if (v > c) != o)
            if best is None or wrong < best[0]:
                best = (wrong, c, off_below)
    wrong, c, off_below = best
    offs = [v for v, o in vals if o]
    oks = [v for v, o in vals if not o]
    margin = (min(oks) - max(offs)) if off_below else (min(offs) - max(oks))
    return {"key": key, "truth": truth, "n": len(vals), "off": len(offs), "threshold": c, "off_side": "below" if off_below else "above",
            "wrong": wrong, "margin": margin, "off_range": (min(offs), max(offs)), "ok_range": (min(oks), max(oks))}


def run_table(work: Path) -> None:
    rows = load_rows(work)
    print(f"{'pair':58s} {'start':6s} {'deg':>4s} acc  doubt     ovl   pick  rmse  fit   dof reach  lever  off_mm first_mm  placed")
    for r in sorted(rows, key=lambda r: (r["scene"], r["result"]["overlap"])):
        res = r["result"]

        def f(x, w=5, d=3):
            return f"{x:{w}.{d}f}" if isinstance(x, (int, float)) and x is not None else " " * (w - 1) + "-"
        print(f"{pair_name(r['a'], r['b']):58s} {r['start']:6s} {r['deg']:4.0f} {'yes' if r['accepted'] else 'no ':3s}  "
              f"{str(res['doubt'] or ''):8s} {f(res['overlap'])} {f(r.get('pick'))} {f(res['rmse_mm'])} {f(res['fit_mm'])} "
              f"{res['dof']:3d} {f(r.get('reach_mm'), 5, 1)} {f(r.get('lever'), 6, 2)} {f(r['error']['median_mm'], 6, 2)} "
              f"{f(r['error_first']['median_mm'], 6, 2)}  {f((r.get('error_placed') or {}).get('median_mm'), 6, 2)}")
    print()
    for truth in ("error", "error_placed"):
        for k in SIGNALS:
            print(json.dumps(separation(rows, k, truth)))


# --------------------------------------------------------------------------- a check after the merge (no truth)
AFTER_MM = (0.3, 0.5, 1.0)


def after_check(merged_mesh: Path, scans: list[tuple[Path, np.ndarray]]) -> dict:
    """Each scan's own part (its mesh's part cloud, table set aside, 1 mm points), moved by its merge pose, against the
    merged mesh: the distance along the merged surface's normal to its nearest vertex. Per scan: the median and the
    share farther than each of :data:`AFTER_MM`; ``worst``: the larger share of the two scans. Needs no truth."""
    from scipy.spatial import cKDTree

    from ohs.align import scan_cloud_from_mesh
    from ohs.mesh.topology import vertex_normals
    from ohs.track.export import load_mesh

    t0 = time.perf_counter()
    V, F, N = load_mesh(merged_mesh)
    if N is None or len(N) != len(V):
        N = vertex_normals(V, F)
    tree = cKDTree(V)
    out: dict = {"scans": []}
    for mesh, T in scans:
        c = scan_cloud_from_mesh(mesh)
        P = G.transform_points(np.asarray(T), c.part.fine.pts)
        d, j = tree.query(P, k=1, distance_upper_bound=5.0)
        far = ~np.isfinite(d)
        dn = np.abs(np.einsum("ij,ij->i", P - V[np.minimum(j, len(V) - 1)], N[np.minimum(j, len(V) - 1)]))
        dn[far] = 5.0
        row = {"n": int(len(P)), "median_mm": float(np.median(dn)), "missing_5mm": float(far.mean())}
        for x in AFTER_MM:
            row[f"over{x:g}"] = float(np.mean(dn > x))
        out["scans"].append(row)
    for x in AFTER_MM:
        out[f"worst_over{x:g}"] = max(r[f"over{x:g}"] for r in out["scans"])
    out["worst_median_mm"] = max(r["median_mm"] for r in out["scans"])
    out["s"] = round(time.perf_counter() - t0, 1)
    return out


def run_after(x7: Path) -> None:
    """:func:`after_check` on X7's saved merges (``x7`` = its ``work``): each merge against the scans it was made of."""
    rows = []
    for pair, (ka, kb, _, _) in MS.PAIRS.items():
        pdir = x7 / "pairs" / pair
        for v in ("a", "p", "b", "b2", "c"):
            done = pdir / f"merge_{v}" / "done.json"
            if not done.is_file():
                continue
            Ts = json.loads(done.read_text(encoding="utf-8"))["T"]
            poses = "true" if v == "c" else "tracked"
            scans = [(MS.scan_dir(x7, ka, poses) / "mesh.ply", np.asarray(Ts[0])), (MS.scan_dir(x7, kb, poses) / "mesh.ply", np.asarray(Ts[1]))]
            r = after_check(pdir / f"merge_{v}" / "mesh.ply", scans)
            r.update(pair=pair, merge=v)
            rows.append(r)
            print(f"{pair} merge_{v}: worst over 0.3/0.5/1.0 mm {r['worst_over0.3']:.3f} {r['worst_over0.5']:.3f} "
                  f"{r['worst_over1']:.3f}, worst median {r['worst_median_mm']:.3f} mm; per scan "
                  + "; ".join(f"med {s['median_mm']:.3f} >0.5 {s['over0.5']:.3f} miss {s['missing_5mm']:.3f}" for s in r["scans"])
                  + f" ({r['s']} s)", flush=True)
    MS.write_json(Path(os.environ.get("OHS_AFTER_OUT", "after_check.json")), rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=("singles", "align", "place", "table", "after"))
    ap.add_argument("work", type=Path, nargs="?")
    ap.add_argument("--scans", type=Path, nargs="*", default=[])
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--starts", nargs="+", default=["shape", "points"])
    ap.add_argument("--recs", default=None)
    a = ap.parse_args(argv)
    if a.what == "singles":
        for scans in SCANS.values():
            for key in scans:
                print(f"python tools/merge_accuracy_study.py single WORK {key} tracked")
        return 0
    if a.work is None:
        ap.error("WORK is needed")
    if a.what == "align":
        run_align(a.work, MS.recs_dir(a), [a.work, *a.scans], a.only, a.starts)
    elif a.what == "after":
        run_after(a.work)
    elif a.what == "place":
        run_place(a.work, MS.recs_dir(a), [a.work, *a.scans])
    else:
        run_table(a.work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
