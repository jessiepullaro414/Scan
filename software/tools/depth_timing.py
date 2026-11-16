"""Where the depth stage's time goes (maintenance plan F9): the stereo matcher alone, and inside
the frame pipeline with and without the laser threads beside it.

    python depth_timing.py <recording> [--scale 0.5] [--frames 60] [--rounds 2]

The recording is any processed-size recording, for example the one ``ohs bench`` keeps
(``~/.ohs/bench/rec-v1-...``). ``--scale 0.5`` is the preview quality, 1.0 the full one.

It prints, per frame (medians, ms):

* **the matcher alone:** the depth estimator's call on rectified frames, nothing else running;
  on the GPU also its kernels' device time (OpenCL profiling events), when this version of the
  engine allows reading them;
* **in the pipeline:** the pipeline's own ``depth`` stage and the whole frame, with the laser
  threads (the normal case), without lasers, and with the laser threads but Python's thread
  switch interval cut from 5 to 0.5 ms (how long the main thread can wait to get Python's lock
  back from a laser thread after the GPU is done).

It only uses calls that older versions of the engine also have, so that two versions can be
compared: run it from a folder outside ``software/`` with ``PYTHONPATH`` pointing at the engine
to time (``python -m`` and a script's own folder come first on the module path). The first
line says which engine was loaded.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path


def median(xs):
    return statistics.median(xs) if xs else float("nan")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("recording", type=Path)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args(argv)

    import ohs
    from ohs.app.pipeline import FramePipeline, PipelineParams
    from ohs.io.recording import RecordingReader

    print(f"engine: {Path(ohs.__file__).parent}")
    rd = RecordingReader(a.recording)
    cal = rd.calibration()
    n = min(int(a.frames), len(rd))
    frames = [rd.frame(k) for k in range(n)]
    skip = min(5, n // 4)  # the first frames build GPU programs and fill caches

    # ---------------------------------------------------------------- the matcher alone
    pipe = FramePipeline(cal, PipelineParams(scale=a.scale))
    try:
        est = pipe.depth_estimator
        with_sigma = bool(getattr(pipe, "has_confidence", False)) and hasattr(est, "compute_with_sigma")
        call = est.compute_with_sigma if with_sigma else est.compute
        pairs = [pipe.rectifier.rectify(f.left, f.right) for f in frames]
        print(f"size {pipe.image_size[0]}x{pipe.image_size[1]}, depth on {getattr(est, 'backend', '?')} "
              f"({getattr(est, 'device', None)}), with sigma: {with_sigma}, {n} frames")
        for L, R in pairs[:skip]:
            call(L, R)
        wall = []
        for _ in range(int(a.rounds)):
            for L, R in pairs[skip:]:
                t = time.perf_counter()
                call(L, R)
                wall.append(1e3 * (time.perf_counter() - t))
        print(f"matcher alone: {median(wall):.2f} ms (median of {len(wall)})")
        m = getattr(est, "_gpu", None)
        if m is not None and hasattr(m, "queue") and hasattr(m, "last_timings_ms"):
            try:
                m.queue = m.cl.CommandQueue(m.queue.context, properties=m.cl.command_queue_properties.PROFILING_ENABLE)
                m.profiling = True
                per: dict[str, list[float]] = {}
                for L, R in pairs[skip:]:
                    call(L, R)
                    for k, v in m.last_timings_ms.items():
                        per.setdefault(k, []).append(v)
                tot = sum(median(v) for v in per.values())
                top = sorted(((median(v), k) for k, v in per.items()), reverse=True)[:6]
                print(f"  its kernels on the device: {tot:.2f} ms in all; " + ", ".join(f"{k} {v:.2f}" for v, k in top))
            except Exception as e:  # an engine version without these attributes
                print(f"  kernel times not available here: {e}")
    finally:
        if hasattr(pipe, "close"):
            pipe.close()

    # ---------------------------------------------------------------- in the pipeline
    cases = [("with the laser threads", True, None), ("without lasers", False, None),
             ("laser threads, 0.5 ms switch interval", True, 0.0005)]
    out: dict[str, dict[str, list[float]]] = {c[0]: {"depth": [], "total": [], "laser_extract": []} for c in cases}
    keep = sys.getswitchinterval()
    for _ in range(int(a.rounds)):
        for name, lasers, interval in cases:
            pipe = FramePipeline(cal, PipelineParams(scale=a.scale, use_lasers=lasers))
            try:
                if interval is not None:
                    sys.setswitchinterval(interval)
                for k, f in enumerate(frames):
                    res = pipe.process(f)
                    if k >= skip:
                        for key in out[name]:
                            if key in res.timings_ms:
                                out[name][key].append(float(res.timings_ms[key]))
            finally:
                sys.setswitchinterval(keep)
                if hasattr(pipe, "close"):
                    pipe.close()
    for name, d in out.items():
        print(f"pipeline, {name}: depth {median(d['depth']):.2f} ms, frame {median(d['total']):.2f} ms"
              + (f", waiting for the lines {median(d['laser_extract']):.2f} ms" if d["laser_extract"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
