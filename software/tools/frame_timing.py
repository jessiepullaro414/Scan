"""Where a frame's time goes with two and with four line lasers on this machine, and whether
work on the processor really runs while the GPU computes the depth (maintenance plan E8, F8).

    python tools/frame_timing.py [--dir DIR] [--sizes 640 1280] [--rounds 2] [--skip-beside]

(run from ``software/``). Simulates four short scans once (sphere on a table, two and four
lasers, 640x400 with 24 frames and 1280x800 with 36; kept in ``--dir``, default
``~/.ohs/frame_timing``) and prints three things:

* **Extraction alone:** ``extract_laser_lines`` on every image of a scan, ms per image: two
  lasers, four lasers, and four lasers with the two helpers as they were before E8
  (``tests/laser_reference.py``, when the tests folder is there).
* **A frame in the pipeline:** the stage medians of ``process_recording`` for the same three
  cases, ``--rounds`` times each, old and new taking turns.
* **Beside the matcher** (GPU path only): how long one extraction takes in a thread alone, beside
  the stereo matcher, and with a second extraction thread as the pipeline runs them; and the same
  extraction in a second process, alone and while this process runs the matcher. Where the
  processor and the GPU share a power budget (an integrated GPU), work beside the matcher is
  several times slower, in the second process too.

The compute path follows ``OHS_COMPUTE`` as the engine does. Results also go to
``<dir>/results/frame_timing-<time>.json``. Run it with nothing else busy on the machine; figures
from different machines are not comparable with each other, only the pattern is.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np

FRAMES = {640: 24, 1280: 36}
HELPERS = ("_row_candidates", "_near_other_lines")
STAGES = ("total", "depth", "laser_extract", "laser_extract_cpu", "laser_triangulate_cpu", "track")


def recording(root: Path, width: int, lasers: int) -> Path:
    """The simulated scan for this size and number of lasers, made once."""
    from ohs.io.recording import RecordingReader
    from ohs.sim.datasets import make_scan_recording

    n = FRAMES[width]
    path = root / f"scan-{width}-{n}f-{lasers}lasers"
    with contextlib.suppress(Exception):
        if len(RecordingReader(path).indices) == n:
            return path
    print(f"simulating {path.name} ...", flush=True)
    return make_scan_recording(path, scene="sphere_on_plane", n_frames=n, image_size=(width, width * 5 // 8), realistic=True,
                               depth_every=0, seed=0, lasers=lasers, overwrite=True)


@contextlib.contextmanager
def old_helpers():
    """The extraction with its helpers as they were before E8; put back afterwards."""
    import ohs.vision.laser as laser

    tests = str(Path(__file__).resolve().parents[1] / "tests")
    sys.path.insert(0, tests)
    try:
        import laser_reference as ref
    finally:
        sys.path.remove(tests)
    kept = {name: getattr(laser, name) for name in HELPERS}
    for name in HELPERS:
        setattr(laser, name, getattr(ref, name))
    try:
        yield
    finally:
        for name, fn in kept.items():
            setattr(laser, name, fn)


def has_reference() -> bool:
    return (Path(__file__).resolve().parents[1] / "tests" / "laser_reference.py").is_file()


def rectified(rec: Path, step: int = 1):
    """``(pipeline, [(left, right), ...])`` for a recording: its rectified images and the
    pipeline whose laser parameters and depth estimator the timings use. Close the pipeline."""
    from ohs.app.pipeline import FramePipeline, PipelineParams
    from ohs.io.recording import RecordingReader

    rd = RecordingReader(rec)
    pipe = FramePipeline(rd.calibration(), PipelineParams())
    pairs = []
    for k in range(0, len(rd.indices), step):
        f = rd.frame(k)
        pairs.append(pipe.rectifier.rectify(f.left, f.right))
    return pipe, pairs


def extraction_alone(rec: Path) -> float:
    """ms per image, the best of three passes over both cameras' images."""
    from ohs.vision.laser import extract_laser_lines

    pipe, pairs = rectified(rec)
    try:
        imgs = [im for pair in pairs for im in pair]
        lp = pipe.laser_params
        best = np.inf
        for _ in range(4):  # the first pass warms up
            t = time.perf_counter()
            for im in imgs:
                extract_laser_lines(im, lp)
            best = min(best, (time.perf_counter() - t) / len(imgs) * 1e3)
        return float(best)
    finally:
        pipe.close()


def frame_stages(rec: Path, work: Path) -> dict[str, float]:
    """Median ms per frame of the pipeline's stages over one ``process_recording`` of the scan."""
    from ohs.app.pipeline import PipelineParams
    from ohs.app.process import ProcessParams, process_recording

    out = work / ("out-" + rec.name)
    shutil.rmtree(out, ignore_errors=True)
    try:
        rep = process_recording(rec, out, params=ProcessParams(pipeline=PipelineParams()))
    finally:
        shutil.rmtree(out, ignore_errors=True)
    return {k: float(rep["timings_ms"][k]["median"]) for k in STAGES if k in rep["timings_ms"]}


def beside_matcher(rec: Path) -> dict[str, dict[str, float]]:
    """One extraction in a thread: alone, beside the matcher, with a second extraction thread
    beside the matcher (as in the pipeline); and the matcher alone. Medians, ms."""
    from ohs.vision.laser import extract_laser_lines

    pipe, pairs = rectified(rec, step=3)
    try:
        lp, est = pipe.laser_params, pipe.depth_estimator
        depth_fn = est.compute_with_sigma if pipe.has_confidence else est.compute

        def extract(img) -> float:
            t = time.perf_counter()
            extract_laser_lines(img, lp)
            return (time.perf_counter() - t) * 1e3

        def depth(left, right) -> float:
            t = time.perf_counter()
            depth_fn(left, right)
            return (time.perf_counter() - t) * 1e3

        for left, right in pairs[:3]:
            depth(left, right)
            extract(left)
        out: dict[str, dict[str, float]] = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            for mode in ("extraction alone", "matcher alone", "one extraction beside the matcher",
                         "two extractions beside the matcher"):
                ex, dp = [], []
                if mode != "extraction alone":  # a GPU that has paused clocks up over its first runs
                    for left, right in pairs[:3]:
                        depth(left, right)
                for left, right in pairs:
                    futs = []
                    if mode != "matcher alone":
                        futs.append(pool.submit(extract, left))
                    if mode.startswith("two"):
                        futs.append(pool.submit(extract, right))
                    if mode != "extraction alone":
                        dp.append(depth(left, right))
                    ex += [f.result() for f in futs]
                out[mode] = {"extraction": float(np.median(ex)) if ex else float("nan"),
                             "matcher": float(np.median(dp)) if dp else float("nan")}
        return out
    finally:
        pipe.close()


def second_process(rec: Path, seconds: float = 2.5) -> dict[str, float]:
    """The extraction in a second process, alone and while this process runs the matcher."""
    pipe, pairs = rectified(rec, step=10**6)  # the first frame is enough
    try:
        est = pipe.depth_estimator
        depth_fn = est.compute_with_sigma if pipe.has_confidence else est.compute
        left, right = pairs[0]
        for _ in range(3):
            depth_fn(left, right)
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child", str(rec), str(3 * seconds)],
                                 stdout=subprocess.PIPE, text=True)
        try:
            line = child.stdout.readline().strip()
            if line != "READY":
                raise RuntimeError(f"the second process did not start: {line!r}")
            time.sleep(seconds)
            a = time.time()
            runs = []
            while time.time() < a + seconds:
                t = time.perf_counter()
                depth_fn(left, right)
                runs.append((time.perf_counter() - t) * 1e3)
            b = time.time()
            data = np.array(json.loads(child.stdout.readline()))
        finally:
            child.wait(timeout=60)
        alone = data[(data[:, 0] < a - 0.2) | (data[:, 0] > b + 0.2), 1]
        beside = data[(data[:, 0] > a + 0.2) & (data[:, 0] < b - 0.2), 1]
        return {"alone": float(np.median(alone)), "beside the matcher": float(np.median(beside)) if len(beside) else float("nan"),
                "runs beside": int(len(beside)), "matcher": float(np.median(runs))}
    finally:
        pipe.close()


def child(rec: Path, seconds: float) -> None:
    """Extract the lines of one image again and again; prints ``[[wall time, ms], ...]``."""
    from ohs.app.pipeline import default_laser_params
    from ohs.io.recording import RecordingReader
    from ohs.vision.laser import extract_laser_lines, lines_per_family
    from ohs.vision.rectify import Rectifier

    rd = RecordingReader(rec)
    cal = rd.calibration()
    rect = Rectifier(cal.stereo, 1.0)
    lp = replace(default_laser_params(rect.image_size[0] / 1280), lines_per_family=lines_per_family(cal.lasers))
    f = rd.frame(len(rd.indices) // 2)
    img = rect.rectify(f.left, f.right)[0]
    for _ in range(5):
        extract_laser_lines(img, lp)
    print("READY", flush=True)
    out = []
    end = time.time() + seconds
    while time.time() < end:
        t = time.perf_counter()
        extract_laser_lines(img, lp)
        out.append((time.time(), (time.perf_counter() - t) * 1e3))
    print(json.dumps(out), flush=True)


def machine() -> dict:
    from ohs import compute

    d = compute.describe()
    dev = d.get("device") or {}
    return {"processor": platform.processor(), "cores": os.cpu_count(), "system": platform.platform(),
            "backend": d.get("backend"), "gpu": dev.get("name"), "gpu_kind": dev.get("kind"), "opencl": dev.get("opencl")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=Path.home() / ".ohs" / "frame_timing", help="folder for the scans and results")
    ap.add_argument("--sizes", type=int, nargs="+", default=[640, 1280], choices=sorted(FRAMES), help="image widths to time")
    ap.add_argument("--rounds", type=int, default=2, help="pipeline runs per case (old and new take turns)")
    ap.add_argument("--skip-beside", action="store_true", help="leave out the timings beside the matcher")
    ap.add_argument("--child", nargs=2, metavar=("RECORDING", "SECONDS"), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.child:
        child(Path(args.child[0]), float(args.child[1]))
        return 0
    root = args.dir
    (root / "results").mkdir(parents=True, exist_ok=True)
    result: dict = {"machine": machine(), "sizes": {}}
    m = result["machine"]
    print(f"machine: {m['processor']} ({m['cores']} threads), compute {m['backend']}: {m['gpu']} ({m['gpu_kind']}, {m['opencl']})")
    ref = has_reference()
    if not ref:
        print("tests/laser_reference.py is not here: no timings with the helpers as they were before E8")
    for width in args.sizes:
        recs = {n: recording(root, width, n) for n in (2, 4)}
        res: dict = {}
        result["sizes"][str(width)] = res
        print(f"\n== {width}x{width * 5 // 8} ({FRAMES[width]} frames) ==")
        alone = {"two lasers": extraction_alone(recs[2]), "four lasers": extraction_alone(recs[4])}
        if ref:
            with old_helpers():
                alone["four lasers, before E8"] = extraction_alone(recs[4])
        res["extraction_alone_ms"] = alone
        print("extraction of one image, alone: " + ", ".join(f"{k} {v:.1f} ms" for k, v in alone.items()))
        cases = [("two lasers", recs[2], False), ("four lasers", recs[4], False)] + ([("four lasers, before E8", recs[4], True)] if ref else [])
        runs: dict[str, list[dict]] = {name: [] for name, _, _ in cases}
        for _ in range(max(1, args.rounds)):
            for name, rec, old in reversed(cases):
                with old_helpers() if old else contextlib.nullcontext():
                    runs[name].append(frame_stages(rec, root))
        res["frame_ms"] = runs
        print("a frame in the pipeline, median ms per run (" + " / ".join(STAGES) + "):")
        for name, rr in runs.items():
            print(f"  {name:24s} " + "   ".join(" / ".join(f"{r.get(k, float('nan')):.1f}" for k in STAGES) for r in rr))
        if not args.skip_beside and m["backend"] == "gpu":
            res["beside"] = {}
            for lasers in (2, 4):
                b = beside_matcher(recs[lasers])
                sp = second_process(recs[lasers])
                res["beside"][str(lasers)] = {"thread": b, "second_process": sp}
                print(f"beside the matcher, {lasers} lasers:")
                for k, v in b.items():
                    print(f"  {k:36s} " + ", ".join(f"{what} {ms:.1f} ms" for what, ms in v.items() if np.isfinite(ms)))
                print(f"  in a second process: alone {sp['alone']:.1f} ms, while this process runs the matcher "
                      f"{sp['beside the matcher']:.1f} ms ({sp['runs beside']} runs; matcher {sp['matcher']:.1f} ms)")
    path = root / "results" / f"frame_timing-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(result, indent=1))
    print(f"\nsaved to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
