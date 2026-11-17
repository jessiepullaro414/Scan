"""Gap and flush on simulated scans against the truth (FEATURE_PLAN I4, the measuring task).

    python tools/gapflush_study.py run <out> [--cases taper taper_flush2 flush_ramp] [--frames 60]
    python tools/gapflush_study.py summary <out>

(run from ``software/``). ``run`` renders each case's recording once (the simulator's
``panel_seam`` scene: two hemmed panels over a dark floor, the gap and flush tapering along the
seam, :data:`CASES`), processes it twice with the app's Standard quality and built-in settings
(:func:`ohs.server.studio.process_params`; the compute path follows ``OHS_COMPUTE``): tracked, and
fused at the true poses. Then it reads gap and flush along the seam from the scene's own two
click points (:func:`ohs.inspect.gapflush.measure_gap_flush`, once per edge rule) and writes each
station with the truth at its place to ``<out>/stations_<case>.jsonl``, and the times to
``<out>/times.json``. A recording or a processed scan already in ``<out>`` is used again, so a run
that stopped can go on. Run one at a time: it is the machine's heavy job while it runs.

``summary`` prints the tables: flush and gap error against the truth per nominal value (mean,
spread along the seam, 95 % of the size of the error, the stated ±, the share within 2σ), tracked
and at the true poses, and per edge rule how the gap's bias changes across the gap sizes (the
stop rule: gap is offered only if that change stays within 0.2 mm).

The station's truth comes from :func:`ohs.sim.scene.panel_seam_truth` at the station's seam
point. A tracked scan is put into the simulator's world at its first tracked frame (as
``report.json``'s trajectory errors are), so its drift is in its readings' place along the seam
(about 1 mm here: 0.02 mm of gap on the taper), not in the readings themselves.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

import numpy as np

#: case name -> (seed, gap at the start and end of the seam, flush at the start and end), mm
CASES = {
    "taper": (0, (2.0, 6.0), (0.0, 0.0)),
    "taper_flush2": (1, (6.0, 2.0), (2.0, 2.0)),
    "flush_ramp": (2, (3.0, 3.0), (0.0, 2.0)),
}
IMAGE_SIZE = (1280, 800)
POSES = ("tracked", "true")


def _scene(case: str):
    from ohs.sim.scene import panel_seam

    seed, gap, flush = CASES[case]
    return panel_seam(seed=seed, gap_mm=gap, flush_mm=flush)


def _params(width: int):
    from ohs.app.config import Config
    from ohs.server.studio import process_params

    pp = process_params(Config(), "markerless", "standard", width)
    return dataclasses.replace(pp, export=("ply",))


def _record(case: str, out: Path, frames: int, times: dict, say) -> Path:
    from ohs.sim.datasets import make_scan_recording

    rec = out / f"rec_{case}"
    if (rec / "ok").is_file():
        return rec
    seed = CASES[case][0]
    say(f"{case}: rendering {frames} frames ...")
    t0 = time.perf_counter()
    make_scan_recording(rec, scene=_scene(case), n_frames=frames, image_size=IMAGE_SIZE, seed=seed, realistic=True,
                        depth_every=0, overwrite=True)
    times[f"{case}/render_s"] = round(time.perf_counter() - t0, 1)
    (rec / "ok").write_text("", encoding="utf-8")
    say(f"{case}: rendered in {times[f'{case}/render_s']} s")
    return rec


def _process(case: str, rec: Path, out: Path, poses: str, times: dict, say) -> Path:
    from ohs.app.process import process_recording
    from ohs.geometry import invert_se3
    from ohs.io.recording import RecordingReader

    dst = out / f"{case}_{poses}"
    if (dst / "mesh.ply").is_file() and (dst / "report.json").is_file():
        return dst
    pp = _params(IMAGE_SIZE[0])
    given = None
    if poses == "true":
        gt = RecordingReader(rec).ground_truth_poses()
        T0inv = invert_se3(gt[min(gt)])
        given = {i: T0inv @ T for i, T in gt.items()}
    say(f"{case}: processing at the {poses} poses ...")
    t0 = time.perf_counter()
    process_recording(rec, dst, params=pp, poses=given)
    times[f"{case}/{poses}_s"] = round(time.perf_counter() - t0, 1)
    say(f"{case}: {poses} poses in {times[f'{case}/{poses}_s']} s")
    return dst


def _to_world(rec: Path, dst: Path, poses: str) -> np.ndarray:
    """The scan's world -> the simulator's (at the first frame that has a pose / was tracked)."""
    from ohs.geometry import invert_se3
    from ohs.io.recording import RecordingReader
    from ohs.track.posegraph import read_trajectory

    gt = RecordingReader(rec).ground_truth_poses()
    if poses == "true":
        return gt[min(gt)]
    rows = [r for r in read_trajectory(dst / "trajectory.csv") if r[2] and r[0] in gt]
    i, _, _, T = rows[0]
    return gt[i] @ invert_se3(T)


def _measure(case: str, rec: Path, dst: Path, poses: str, say) -> list[dict]:
    from ohs.inspect.gapflush import EDGE_RULES, measure_gap_flush
    from ohs.sim.scene import panel_seam_truth
    from ohs.track.export import load_ply
    from scipy.spatial import cKDTree

    sc = _scene(case)
    m = load_ply(dst / "mesh.ply")
    A = _to_world(rec, dst, poses)
    V = m["vertices"] @ A[:3, :3].T + A[:3, 3]
    F = m["faces"]
    tree = cKDTree(V)
    clicks = [V[tree.query(c)[1]] for c in sc.meta["clicks"]]  # the mesh's vertex nearest each true click point
    rep = json.loads((dst / "report.json").read_text(encoding="utf-8"))
    traj = rep.get("ground_truth", {}).get("trans_mm", {})
    rows = []
    for rule in EDGE_RULES:
        t0 = time.perf_counter()
        res = measure_gap_flush(V, F, clicks[0], clicks[1], sigma=m["quality"], filled=m["filled"], rule=rule)
        dt = time.perf_counter() - t0
        X = np.array([s.point for s in res.stations])
        truth = panel_seam_truth(sc, X)
        for k, s in enumerate(res.stations):
            d = s.to_dict()
            d.update({"case": case, "poses": poses, "rule": rule, "true_gap_mm": float(truth["gap_mm"][k]),
                      "true_flush_mm": float(truth["flush_mm"][k]), "along_mm": float(truth["along_mm"][k]),
                      "on_seam": bool(truth["on_seam"][k]), "correlated_points": res.correlated_points,
                      "measure_s": dt, "trajectory_error_mm": traj})
            rows.append(d)
        say(f"{case} {poses} {rule}: {len(res.stations)} stations, {res.gap.n_trusted} gaps and "
            f"{res.flush.n_trusted} flushes trusted, N_c {res.correlated_points:.2f}, {dt:.1f} s")
    return rows


def run(out: Path, cases: list[str], frames: int, say=print) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tpath = out / "times.json"
    times = json.loads(tpath.read_text(encoding="utf-8")) if tpath.is_file() else {}
    for case in cases:
        rec = _record(case, out, frames, times, say)
        tpath.write_text(json.dumps(times, indent=1), encoding="utf-8")
        rows = []
        for poses in POSES:
            dst = _process(case, rec, out, poses, times, say)
            tpath.write_text(json.dumps(times, indent=1), encoding="utf-8")
            rows += _measure(case, rec, dst, poses, say)
        path = out / f"stations_{case}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        say(f"{len(rows)} station rows -> {path}")


# --------------------------------------------------------------------------- the tables
def _row(err: np.ndarray, sig: np.ndarray) -> str:
    if len(err) == 0:
        return "| – | – | – | – | – |"
    cover = np.mean(np.abs(err) <= 2 * sig) if np.isfinite(sig).all() else float("nan")
    return (f"| {err.mean():+.3f} | {err.std():.3f} | {np.percentile(np.abs(err), 95):.3f} | {np.median(sig):.3f} "
            f"| {100 * cover:.0f} % |")


def summary(out: Path, say=print) -> None:
    from ohs.inspect.gapflush import DEFAULT_RULE, EDGE_RULES

    rows = [json.loads(x) for p in sorted(out.glob("stations_*.jsonl"))
            for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if r["on_seam"]]
    say("## Stations\n")
    say("| Case | Poses | Stations | Gap trusted | Flush trusted | Bridged | A / B not measured to the edge | N_c |")
    say("|---|---|---|---|---|---|---|---|")
    for case in CASES:
        for poses in POSES:
            rr = [r for r in rows if r["case"] == case and r["poses"] == poses and r["rule"] == DEFAULT_RULE]
            if not rr:
                continue
            na = sum(r["side_a"] != "measured" for r in rr)
            nb = sum(r["side_b"] != "measured" for r in rr)
            say(f"| {case} | {poses} | {len(rr)} | {sum(r['gap_trusted'] for r in rr)} | "
                f"{sum(r['flush_trusted'] for r in rr)} | {sum(r['bridged'] for r in rr)} | {na} / {nb} | "
                f"{rr[0]['correlated_points']:.2f} |")
    say("\n## Flush (trusted stations, rule " + DEFAULT_RULE + "): error = reading - truth, mm\n")
    say("| Poses | Nominal flush | Stations | Mean error | Spread (sd) | 95 % of abs error | Stated ± (median) | Within 2σ |")
    say("|---|---|---|---|---|---|---|---|")
    for poses in POSES:
        rr = [r for r in rows if r["poses"] == poses and r["rule"] == DEFAULT_RULE and r["flush_trusted"]]
        for nom in (0.0, 0.5, 1.0, 1.5, 2.0):
            sel = [r for r in rr if abs(r["true_flush_mm"] - nom) <= 0.25]
            err = np.array([r["flush_mm"] - r["true_flush_mm"] for r in sel])
            sig = np.array([r["flush_sigma_mm"] for r in sel])
            say(f"| {poses} | {nom:g} | {len(sel)} " + _row(err, sig))
    for rule in EDGE_RULES:
        say(f"\n## Gap by rule {rule} (every station with a reading, trusted or not): error = reading - truth, mm\n")
        say("| Poses | Nominal gap | Stations (trusted) | Mean error (bias) | Spread (sd) | 95 % of abs error "
            "| Stated ± (median) | Within 2σ |")
        say("|---|---|---|---|---|---|---|---|")
        for poses in POSES:
            rr = [r for r in rows if r["poses"] == poses and r["rule"] == rule and r["gap_mm"] is not None]
            means = []
            for nom in (2.0, 3.0, 4.0, 5.0, 6.0):
                sel = [r for r in rr if abs(r["true_gap_mm"] - nom) <= 0.5]
                err = np.array([r["gap_mm"] - r["true_gap_mm"] for r in sel])
                sig = np.array([r["gap_sigma_mm"] for r in sel])
                if len(err):
                    means.append(err.mean())
                say(f"| {poses} | {nom:g} | {len(sel)} ({sum(r['gap_trusted'] for r in sel)}) " + _row(err, sig))
            if means:
                say(f"| {poses} | **bias change across gaps** | | **{max(means) - min(means):.3f}** | | | | |")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gapflush_study", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="render, process and measure the cases")
    r.add_argument("out", type=Path)
    r.add_argument("--cases", nargs="+", choices=sorted(CASES), default=list(CASES))
    r.add_argument("--frames", type=int, default=60)
    s = sub.add_parser("summary", help="print the tables")
    s.add_argument("out", type=Path)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the tables have ± and σ; a Windows console's code page has not
    if args.cmd == "run":
        run(args.out, args.cases, args.frames)
    else:
        summary(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
