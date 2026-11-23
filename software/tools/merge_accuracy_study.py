"""Is a merge of two scans more accurate than the better of them? (ENGINE_PLAN X7)

    python tools/merge_accuracy_study.py single WORK SCAN {tracked,true} [--rep K]
    python tools/merge_accuracy_study.py pair WORK PAIR [--only a p b b2 c] [--rep K]
    python tools/merge_accuracy_study.py judge WORK PAIR [--rep K]
    python tools/merge_accuracy_study.py table WORK

(run from ``software/``; ``--recs`` is the scorecard cache's ``recs`` folder, by default
``%OHS_SCORECARD%\\recs``; the recordings must be there: ``tools/scorecard.py render``.) Five pairs of scans
(:data:`PAIRS`), each scan a stretch of 90 frames of one of the scorecard's 400-degree orbits, from two
different seeds: the same part, not moved, scanned twice from two sides. Each scan is ``SCAN = name@start``.
Every graphics-card step is one process: run them one at a time (``VALIDATION.md``, X7). The order:
``single`` for the ten scans, tracked and true; ``pair`` for each pair; ``judge``; ``table``.

* ``single``: one scan as Make mesh makes it (``scorecard.app_chain``: Follow the camera, Straighten, Refine
  with lasers), tracked or at its true poses.
* ``pair``: the alignment (``ohs.align.align_pair``, same setup, on the two tracked scans' meshes; pair P3 from
  points "picked" on both scans, true matches 1.5 mm off) and the merges as the app makes them
  (``ohs.align.merge_scans`` with the server's merge settings): (a) tracked scans at the aligned pose, (p) at
  the pose aligned from picked points (what the app asks for when it is not sure), (b) tracked scans at the
  true motion between their first cameras, (b2) at the true motion between the scans' worlds as all their
  frames place them (a tracked scan's first frame can be millimetres off the rest), (c) both scans at their
  true poses (the engine's ceiling).
* ``judge``: every mesh with the Default recipe (as the scorecard), placed in the simulator's world by the part
  alone (``scorecard.place_part``), scored on the part only: median distance, share more than 0.3 mm off, the
  count of vertices within 0.5 mm, and ``cover05``: the share of the true surface either scan saw (pooled
  samples, the same for every mesh of the pair) with the mesh within 0.5 mm. The union of the two single scans
  is a sample covered by either. Thin walls and fits from ``scorecard.scene_fits``.
* ``table``: the tables, and per pair whether the app's merge (``a``, or ``p`` where the alignment was not
  sure) beats the better single scan (the one with fewer vertices more than 0.3 mm off): median and share both
  no worse than it by more than :data:`SPREAD`, one of them better by more than it; and whether its cover05
  reaches the union's (less :data:`SPREAD`). Two runs of the same job gave identical numbers on this PC, so
  the spread is the floor.

Everything goes under ``WORK``; a step that has its result is not run again.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from ohs import geometry as G  # noqa: E402
from tools import scorecard as SC  # noqa: E402

LENGTH = 90  # frames a scan: 9 s at 10 fps, a sweep of 140-160 degrees
#: pair: (scan A, scan B, how the alignment starts, what the pair is)
PAIRS: dict[str, tuple[str, str, str, str]] = {
    "P1": ("bracket240-s0@0", "bracket240-s2@140", "shape", "bracket, 97 deg apart"),
    "P2": ("bracket240-s3@0", "bracket240-s0@130", "shape", "bracket, 56 deg apart"),
    "P3": ("bracket240-s4@0", "bracket240-s1@20", "points", "bracket, opposite sides (171 deg)"),
    "P4": ("panel200-s4@0", "panel200-s0@70", "shape", "car panel, 99 deg apart"),
    "P5": ("sheet-s0@110", "sheet-s2@50", "shape", "sheet part (thin walls), 123 deg apart"),
}
PICK_NOISE_MM = 1.5
PICKS = 4
GOOD_MM = 0.5
#: what counts as a change (median mm, share more than 0.3 mm, cover05): two runs gave identical numbers
SPREAD = {"part_med": 0.003, "part_over03": 0.005, "cover05": 0.005}


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def recs_dir(a: argparse.Namespace) -> Path:
    if a.recs:
        return Path(a.recs)
    return Path(os.environ.get("OHS_SCORECARD") or SC.DEFAULT_CACHE) / "recs"


def scan_spec(key: str) -> tuple[SC.SceneSpec, int, int]:
    """The scorecard recording of a scan ``name@start`` and its stretch."""
    name, start = key.split("@")
    base = SC.SCENES[name]
    s = int(start)
    return dataclasses.replace(base, name=key), s, s + LENGTH


def _patch_stretch(key: str) -> None:
    """``scorecard.stretch`` gives this scan's frames (the app chain and the judge read it)."""
    _, s, e = scan_spec(key)
    orig = SC.stretch

    def stretch(spec: SC.SceneSpec) -> tuple[int, int | None]:
        return (s, e) if spec.name == key else orig(spec)

    SC.stretch = stretch


def write_json(path: Path, obj: Any) -> None:
    SC.write_json(path, SC._plain(obj))


def scan_dir(work: Path, key: str, poses: str, rep: int = 0) -> Path:
    return work / "scans" / (key.replace("@", "_") + (f"_r{rep}" if rep else "")) / poses


# --------------------------------------------------------------------------- one scan
def run_single(work: Path, recs: Path, key: str, poses: str, rep: int = 0) -> Path:
    from ohs.io.recording import RecordingReader

    spec, s, e = scan_spec(key)
    out = scan_dir(work, key, poses, rep)
    if (out / "done.json").is_file():
        return out
    _patch_stretch(key)
    rec = recs / spec.name.split("@")[0]
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    gt = RecordingReader(rec).ground_truth_poses()
    given = {i: T for i, T in gt.items() if s <= i < e} if poses == "true" else None
    t0 = time.perf_counter()
    rep_, info = SC.app_chain(rec, out, spec, {}, given)
    info["wall_s"] = round(time.perf_counter() - t0, 1)
    info["ground_truth"] = rep_.get("ground_truth")
    shutil.rmtree(out / "stages", ignore_errors=True)
    write_json(out / "done.json", info)
    log(f"{key} {poses}: {info['wall_s']} s; stages {json.dumps(SC._plain({k: info.get(k) for k in ('straighten', 'refine')}))[:400]}")
    return out


# --------------------------------------------------------------------------- the pair
def first_camera(rec: Path, traj: Path) -> np.ndarray:
    """The scan's world in the simulator's, through its first tracked frame."""
    from ohs.io.recording import RecordingReader

    gt = RecordingReader(rec).ground_truth_poses()
    rows = [(i, T) for i, T in SC._traj(traj) if i in gt]
    i0, T0 = rows[0]
    return gt[i0] @ G.invert_se3(T0)


def fitted_world(rec: Path, traj: Path, ahead_mm: float = 200.0) -> np.ndarray:
    """The scan's world in the simulator's that best fits all its tracked frames (a rigid fit of each
    camera's centre and of a point ``ahead_mm`` in front of it onto the true ones). A tracked scan's first
    frame can sit millimetres off the rest (ENGINE_PLAN X4); this does not hang on it."""
    from ohs.io.recording import RecordingReader

    gt = RecordingReader(rec).ground_truth_poses()
    rows = [(i, T) for i, T in SC._traj(traj) if i in gt]
    P, Q = [], []
    for i, T in rows:
        for z in (0.0, ahead_mm):
            P.append(T[:3, 3] + z * T[:3, 2])
            Q.append(gt[i][:3, 3] + z * gt[i][:3, 2])
    return SC._kabsch(np.asarray(P), np.asarray(Q))


def merge_params():
    """The server's merge settings (``ohs.server.aligning.AlignManager.merge_params``, General preset)."""
    from ohs.app.config import Config
    from ohs.server.studio import process_params

    return dataclasses.replace(process_params(Config(), "markerless", "standard", SC.IMAGE_SIZE[0]), poisson=False)


def picked_start(A: Path, B: Path, T_true: np.ndarray, seed: int = 0) -> tuple[np.ndarray, float, list]:
    """Points a user would pick: ``PICKS`` places on the part that both meshes hold (true matches, each
    picked ``PICK_NOISE_MM`` off on each scan), spread out; the pose they give (B's world into A's)."""
    from scipy.spatial import cKDTree

    from ohs.align.pairwise import scan_cloud_from_mesh
    from ohs.align import pose_from_point_pairs
    from ohs.track.export import load_mesh

    Va, _, _ = load_mesh(A / "mesh.ply")
    Vb, _, _ = load_mesh(B / "mesh.ply")
    sup = scan_cloud_from_mesh(A / "mesh.ply").support
    Vb_in_a = G.transform_points(T_true, Vb)
    d, _ = cKDTree(Vb_in_a).query(Va, distance_upper_bound=1.0)
    ok = np.isfinite(d)
    if sup is not None:
        n, dd = sup
        ok &= (Va @ np.asarray(n) + float(dd)) > 5.0  # on the part, not the table
    P = Va[ok]
    rng = np.random.default_rng(seed)
    pick = [P[rng.integers(len(P))]]
    for _ in range(PICKS - 1):  # farthest point sampling
        dist = np.min(np.linalg.norm(P[:, None, :] - np.asarray(pick)[None], axis=2), axis=1)
        pick.append(P[int(np.argmax(dist))])
    dst = np.asarray(pick) + rng.normal(0.0, PICK_NOISE_MM / np.sqrt(3), (PICKS, 3))
    src = G.transform_points(G.invert_se3(T_true), np.asarray(pick)) + rng.normal(0.0, PICK_NOISE_MM / np.sqrt(3), (PICKS, 3))
    T, rms = pose_from_point_pairs(src, dst)
    return T, rms, [src.tolist(), dst.tolist()]


def pose_error(T: np.ndarray, T_true: np.ndarray, V: np.ndarray) -> dict:
    """How far a pose puts the source scan's points from where the true motion puts them."""
    d = np.linalg.norm(G.transform_points(T, V) - G.transform_points(T_true, V), axis=1)
    return {"median_mm": float(np.median(d)), "max_mm": float(d.max()),
            "rot_deg": float(G.rotation_angle_deg(T[:3, :3] @ T_true[:3, :3].T))}


def run_pair(work: Path, recs: Path, pair: str, only: list[str], rep: int = 0) -> None:
    from ohs.align import ScanInput, align_pair, merge_scans, scan_cloud_from_mesh
    from ohs.track.export import load_mesh

    ka, kb, how, _ = PAIRS[pair]
    pdir = work / "pairs" / (pair + (f"_r{rep}" if rep else ""))
    pdir.mkdir(parents=True, exist_ok=True)
    A, B = scan_dir(work, ka, "tracked"), scan_dir(work, kb, "tracked")
    At, Bt = scan_dir(work, ka, "true"), scan_dir(work, kb, "true")
    ra, rb = recs / ka.split("@")[0], recs / kb.split("@")[0]
    Wa, Wb = first_camera(ra, A / "trajectory.csv"), first_camera(rb, B / "trajectory.csv")
    T_true = G.invert_se3(Wa) @ Wb  # B's tracked world into A's, through the first cameras
    al_path = pdir / "align.json"
    if not al_path.is_file() and "a" in only:
        t0 = time.perf_counter()
        dst, src = scan_cloud_from_mesh(A / "mesh.ply"), scan_cloud_from_mesh(B / "mesh.ply")
        init, rms, picks = (None, None, None)
        if how == "points":
            init, rms, picks = picked_start(A, B, T_true)
        res = align_pair(src, dst, init=init, same_setup=True)
        Vb, _, _ = load_mesh(B / "mesh.ply")
        out = {"result": res.as_dict(), "T": res.T.tolist(), "accepted": bool(res.accepted), "T_true": T_true.tolist(),
               "error": pose_error(res.T, T_true, Vb[::7]), "start": how, "picks_rms_mm": rms, "picks": picks,
               "init_error": None if init is None else pose_error(init, T_true, Vb[::7]), "s": time.perf_counter() - t0}
        write_json(al_path, out)
        log(f"{pair} align: accepted {res.accepted}, {out['error']}, {res.verdict}")
    pts_path = pdir / "align_points.json"
    if not pts_path.is_file() and "p" in only:  # what the app asks for when it is not sure: picked points
        t0 = time.perf_counter()
        dst, src = scan_cloud_from_mesh(A / "mesh.ply"), scan_cloud_from_mesh(B / "mesh.ply")
        init, rms, picks = picked_start(A, B, T_true)
        res = align_pair(src, dst, init=init, same_setup=True)
        Vb, _, _ = load_mesh(B / "mesh.ply")
        write_json(pts_path, {"result": res.as_dict(), "T": res.T.tolist(), "accepted": bool(res.accepted), "T_true": T_true.tolist(),
                              "error": pose_error(res.T, T_true, Vb[::7]), "start": "points", "picks_rms_mm": rms,
                              "init_error": pose_error(init, T_true, Vb[::7]), "s": time.perf_counter() - t0})
        log(f"{pair} align from points: accepted {res.accepted}, {res.verdict}")
    params = merge_params()
    for v in only:
        mdir = pdir / f"merge_{v}"
        if (mdir / "done.json").is_file():
            continue
        if v in ("a", "p"):
            T = np.asarray(json.loads((al_path if v == "a" else pts_path).read_text(encoding="utf-8"))["T"])
            inputs = [ScanInput(ra, A / "trajectory.csv", np.eye(4), "A"), ScanInput(rb, B / "trajectory.csv", T, "B")]
        elif v == "b":
            inputs = [ScanInput(ra, A / "trajectory.csv", np.eye(4), "A"), ScanInput(rb, B / "trajectory.csv", T_true, "B")]
        elif v == "b2":  # the true motion between the scans' worlds as all their frames place them
            T_fit = G.invert_se3(fitted_world(ra, A / "trajectory.csv")) @ fitted_world(rb, B / "trajectory.csv")
            inputs = [ScanInput(ra, A / "trajectory.csv", np.eye(4), "A"), ScanInput(rb, B / "trajectory.csv", T_fit, "B")]
        else:  # true poses: both scans in the simulator's world already
            inputs = [ScanInput(ra, At / "trajectory.csv", np.eye(4), "A"), ScanInput(rb, Bt / "trajectory.csv", np.eye(4), "B")]
        shutil.rmtree(mdir, ignore_errors=True)
        t0 = time.perf_counter()
        r = merge_scans(inputs, mdir, params)
        write_json(mdir / "done.json", {"s": time.perf_counter() - t0, "T": [x.T.tolist() for x in inputs],
                                        "vertices": r["mesh"]["n_vertices"], "warnings": r.get("warnings")})
        log(f"{pair} merge {v}: {time.perf_counter() - t0:.1f} s, {r['mesh']['n_vertices']} vertices")


# --------------------------------------------------------------------------- judging
def score_mesh(spec, sc, truth, mesh: Path, T0: np.ndarray, seen: np.ndarray) -> tuple[dict, np.ndarray, np.ndarray]:
    """One mesh (module docstring): ``(metrics, distance of each seen sample to the mesh, good cells)``."""
    from ohs.mesh.order import canonical
    from ohs.track.export import load_ply
    from tools import preset_study as ps

    d = load_ply(mesh)
    c = canonical(np.asarray(d["vertices"], np.float64), np.asarray(d["faces"], np.int64))
    V, F, S = c.V, c.F, c.vertex_values(d["quality"])
    V2, F2, S2, Fl2, _ = ps.apply_ops(V, F, S, ps.RECIPES["general"])
    T, pinfo = SC.place_part(V2, T0, truth)
    Vs = G.transform_points(T, V2)
    M = SC.part_scores(Vs, truth)
    dist, part, _ = truth.nearest(Vs)
    good = part & (dist <= GOOD_MM)
    M["good_n"] = int(good.sum())
    cells = np.unique(np.floor(Vs[good]).astype(np.int64), axis=0)
    M["good_cells"] = int(len(cells))
    cover = SC.mesh_distance(Vs, F2, seen)
    M["cover05"] = float(np.mean(cover < GOOD_MM))
    M["cover1"] = float(np.mean(cover < 1.0))
    M["faces"] = int(len(F2))
    M["placement_moved_mm"] = pinfo.get("moved_mm")
    fits = SC.scene_fits(spec, sc, Vs, F2, S2, Fl2, seen, cover)
    M.update({k: v for k, v in fits.items() if not k.startswith("_")})
    return M, cover, cells


def run_judge(work: Path, recs: Path, pair: str, rep: int = 0) -> dict:
    from tools import preset_study as ps

    ka, kb, _, what = PAIRS[pair]
    pdir = work / "pairs" / (pair + (f"_r{rep}" if rep else ""))
    spec_a, sa, ea = scan_spec(ka)
    _, sb, eb = scan_spec(kb)
    sc = SC.scene_of(spec_a)
    truth = SC.truth_for(spec_a, sc)
    ra, rb = recs / ka.split("@")[0], recs / kb.split("@")[0]
    seen = np.concatenate([ps.seen_points(sc, ra, sa, ea, every=3), ps.seen_points(sc, rb, sb, eb, every=3)])
    if len(seen) > 200_000:
        seen = seen[np.random.default_rng(0).choice(len(seen), 200_000, replace=False)]
    out: dict[str, Any] = {"pair": pair, "what": what, "seen_n": int(len(seen)), "rows": {}}
    covers, cellsets = {}, {}
    A, B = scan_dir(work, ka, "tracked"), scan_dir(work, kb, "tracked")
    if rep and (scan_dir(work, ka, "tracked", rep) / "done.json").is_file():  # a repeat of scan A, for the spread
        A = scan_dir(work, ka, "tracked", rep)
    jobs = [("A", A / "mesh.ply", fitted_world(ra, A / "trajectory.csv")),
            ("B", B / "mesh.ply", fitted_world(rb, B / "trajectory.csv")),
            ("A true", scan_dir(work, ka, "true") / "mesh.ply", np.eye(4)),
            ("B true", scan_dir(work, kb, "true") / "mesh.ply", np.eye(4))]
    for v in ("a", "p", "b", "b2"):
        jobs.append((f"merge {v}", pdir / f"merge_{v}" / "mesh.ply", jobs[0][2]))
    jobs.append(("merge c", pdir / "merge_c" / "mesh.ply", np.eye(4)))
    for name, mesh, T0 in jobs:
        if not mesh.is_file():
            continue
        t0 = time.perf_counter()
        M, cov, cells = score_mesh(spec_a, sc, truth, mesh, T0, seen)
        out["rows"][name] = M
        covers[name], cellsets[name] = cov, cells
        log(f"{pair} {name}: {time.perf_counter() - t0:.0f} s; med {M['part_med']:.3f} over03 {M['part_over03']:.3f} "
            f"good {M['good_n']} cells {M['good_cells']} cover05 {M['cover05']:.3f}")
    for x, y, name in (("A", "B", "union"), ("A true", "B true", "union true")):
        if x in covers and y in covers:
            u = np.minimum(covers[x], covers[y])
            cells = np.unique(np.vstack([cellsets[x], cellsets[y]]), axis=0)
            out["rows"][name] = {"cover05": float(np.mean(u < GOOD_MM)), "cover1": float(np.mean(u < 1.0)), "good_cells": int(len(cells))}
    out["world_fit"] = {"A_first_vs_fit": pose_error(jobs[0][2], first_camera(ra, A / "trajectory.csv"), np.zeros((1, 3))),
                        "B_first_vs_fit": pose_error(jobs[1][2], first_camera(rb, B / "trajectory.csv"), np.zeros((1, 3)))}
    al = pdir / "align.json"
    if al.is_file():
        out["align"] = json.loads(al.read_text(encoding="utf-8"))
        out["align"].pop("picks", None)
        from ohs.track.export import load_mesh

        Vb, _, _ = load_mesh(B / "mesh.ply")
        T_fit = G.invert_se3(jobs[0][2]) @ jobs[1][2]
        out["align"]["error_vs_fit"] = pose_error(np.asarray(out["align"]["T"]), T_fit, Vb[::7])
        if (pdir / "align_points.json").is_file():
            out["align_points"] = json.loads((pdir / "align_points.json").read_text(encoding="utf-8"))
            out["align_points"]["error_vs_fit"] = pose_error(np.asarray(out["align_points"]["T"]), T_fit, Vb[::7])
    write_json(pdir / "judge.json", out)
    return out


# --------------------------------------------------------------------------- tables
ROW_KEYS = ("part_n", "part_med", "part_over03", "part_signed", "good_n", "good_cells", "cover05")


def run_table(work: Path) -> None:
    for pair in PAIRS:
        f = work / "pairs" / pair / "judge.json"
        if not f.is_file():
            continue
        j = json.loads(f.read_text(encoding="utf-8"))
        print(f"\n{pair}: {j['what']}")
        al = j.get("align") or {}
        if al:
            print(f"  alignment: accepted {al.get('accepted')}, off {al.get('error')}, {al['result'].get('verdict')}")
        for name, M in j["rows"].items():
            cells = " ".join(f"{k}={SC.fmt(M.get(k))}" for k in ROW_KEYS if k in M)
            extra = " ".join(f"{k}={SC.fmt(v)}" for k, v in M.items() if k.startswith(("sheet_", "br_", "hole25_")) and v is not None)
            print(f"  {name:12s} {cells} {extra}")
        for merge, union, singles in (("merge a", "union", ("A", "B")), ("merge c", "union", ("A", "B"))):
            print("  " + verdict(j["rows"], merge if merge != "merge a" or al.get("accepted") or "merge p" not in j["rows"]
                                 else "merge p", union, singles))


def verdict(rows: dict, merge: str, union: str, singles: tuple[str, str]) -> str:
    """Whether ``merge`` beats the better of ``singles`` and reaches ``union`` (module docstring, ``table``)."""
    if merge not in rows or not all(s in rows for s in singles):
        return f"{merge}: not there"
    best = min(singles, key=lambda s: (rows[s]["part_over03"], rows[s]["part_med"]))
    m, b = rows[merge], rows[best]
    d = {k: m[k] - b[k] for k in ("part_med", "part_over03")}
    beats = all(v <= SPREAD[k] for k, v in d.items()) and any(v < -SPREAD[k] for k, v in d.items())
    reach = m["cover05"] >= rows[union]["cover05"] - SPREAD["cover05"]
    return (f"{merge} against {best} (the better single): median {d['part_med']:+.4f} mm, share over 0.3 mm "
            f"{100 * d['part_over03']:+.1f} points: {'BEATS' if beats else 'does not beat'}; cover05 "
            f"{m['cover05']:.3f} against the union's {rows[union]['cover05']:.3f}: {'reaches' if reach else 'short of'} it")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=("single", "pair", "judge", "table"))
    ap.add_argument("work", type=Path)
    ap.add_argument("args", nargs="*")
    ap.add_argument("--only", nargs="+", default=["a", "b", "c"])
    ap.add_argument("--rep", type=int, default=0)
    ap.add_argument("--recs", default=None)
    a = ap.parse_args(argv)
    recs = recs_dir(a)
    if a.what == "single":
        run_single(a.work, recs, a.args[0], a.args[1], a.rep)
    elif a.what == "pair":
        run_pair(a.work, recs, a.args[0], a.only, a.rep)
    elif a.what == "judge":
        run_judge(a.work, recs, a.args[0], a.rep)
    else:
        run_table(a.work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
