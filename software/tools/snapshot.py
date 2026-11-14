"""Before/after snapshots of the engine output (maintenance task A2).

    python tools/snapshot.py snapshot <dir> [--frames N] [--qualities preview full] [--recording DIR]
    python tools/snapshot.py compare <a> <b> [--tol X]

(run from ``software/``; ``python -m tools.snapshot ...`` works too).

``snapshot`` processes the simulated recording that ``ohs bench`` uses (cached under the bench
folder, simulated once if missing) with the same parameters the app uses for each quality
(:func:`ohs.server.studio.process_params`, built-in settings) through
:func:`ohs.app.process.process_recording`, and writes ``<dir>/<quality>.npz`` with

* ``poses`` (n,4,4 float64), ``pose_index`` and ``pose_ok``: the trajectory, raw (not the rounded csv),
* ``vertices`` (float32) and ``faces``: the final mesh as exported, in canonical order (vertices
  sorted, faces rotated and sorted: the engine returns the same mesh in a different vertex order
  on every run),
* ``laser_<name>``: the validated laser points of every frame, in frame order (frame C, mm),
* ``_meta``: JSON text with the quality, frames, git commit, compute device and recording.

The compute path follows ``OHS_COMPUTE`` exactly as the engine does. Snapshots are only comparable
on the same machine and compute path (``compare`` warns when the device differs); never commit them.
On the CPU path a snapshot holds Open3D to one thread while it processes (unless
``OHS_O3D_THREADS`` is set, which then decides): only then do two runs of the same code give the
same snapshot there. It takes about twice as long. The limit is put back afterwards.

``compare`` takes two ``.npz`` files or two folders with the same ``<quality>.npz`` files. It prints
the largest absolute difference per array, reports shape mismatches and missing arrays, and exits
0 only if everything is identical (or within ``--tol``).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

QUALITIES = ("preview", "full")
META_KEY = "_meta"


# --------------------------------------------------------------------------- snapshot
def device_description() -> str:
    """One line naming the compute device the engine will use (``cpu`` without a GPU)."""
    from ohs import compute

    d = compute.describe()
    dev = d.get("device")
    if not dev:
        return "cpu"
    return f"{dev.get('name')} ({dev.get('kind')}, {dev.get('opencl')})"


def canonical_mesh(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The same mesh in a fixed order: vertices sorted by (x, y, z), each face rotated (orientation kept)
    to start at its smallest index, faces sorted. The engine's mesh extraction returns the same
    vertices in a different order from run to run, which would make every snapshot differ."""
    if len(V) == 0 or len(F) == 0:
        return V, F
    order = np.lexsort((V[:, 2], V[:, 1], V[:, 0]))
    inv = np.empty(len(V), dtype=np.int64)
    inv[order] = np.arange(len(V))
    F = inv[F]
    shift = np.argmin(F, axis=1)
    F = np.take_along_axis(F, (shift[:, None] + np.arange(3)[None, :]) % 3, axis=1)
    F = F[np.lexsort((F[:, 2], F[:, 1], F[:, 0]))]
    return V[order], F


def take_snapshot(rec: str | Path, quality: str, frames: int | None = None) -> tuple[dict[str, np.ndarray], dict]:
    """Process the first ``frames`` frames (all if ``None``) of ``rec`` at ``quality``.

    Returns ``(arrays, meta)``.
    """
    from ohs.app.bench import _git_commit
    from ohs.app.config import Config
    from ohs.app.process import process_recording
    from ohs.io.recording import RecordingReader
    from ohs.server.studio import process_params
    from ohs.track.export import load_ply

    import dataclasses

    width = int(RecordingReader(rec).calibration().stereo.image_size[0])
    pp = process_params(Config(), "markerless", quality, width)
    pp = dataclasses.replace(pp, export=("ply",), stop=frames)

    poses: list[np.ndarray] = []
    index: list[int] = []
    ok: list[bool] = []
    lasers: dict[str, list[np.ndarray]] = {}

    def on_frame(k: int, n: int, res) -> None:
        poses.append(np.array(res.track.T_wc, dtype=np.float64).reshape(4, 4))
        index.append(int(res.index))
        ok.append(bool(res.track.ok))
        for name, pts in (res.laser_points or {}).items():
            lasers.setdefault(str(name), []).append(np.array(pts))

    device = device_description()
    # the CPU path's Open3D model repeats itself only on one thread (ohs.track.markerless): a
    # snapshot is for comparing, so exact beats fast here. Only while this snapshot is processed:
    # the limit is process-wide. A limit the caller set with OHS_O3D_THREADS is left as it is.
    one_thread = device == "cpu" and not os.environ.get("OHS_O3D_THREADS", "").strip()
    t0 = time.perf_counter()
    with contextlib.ExitStack() as stack:
        if one_thread:
            from ohs.track.markerless import open3d_threads

            stack.enter_context(open3d_threads(1))
        tmp = stack.enter_context(tempfile.TemporaryDirectory(prefix="ohs-snapshot-"))
        report = process_recording(rec, tmp, params=pp, progress=on_frame)
        mesh = load_ply(Path(tmp) / "mesh.ply")
    seconds = time.perf_counter() - t0

    V = (np.zeros((0, 3)) if mesh["vertices"] is None else mesh["vertices"]).astype(np.float32)
    F = (np.zeros((0, 3)) if mesh["faces"] is None else mesh["faces"]).astype(np.int64)
    V, F = canonical_mesh(V, F)
    arrays: dict[str, np.ndarray] = {
        "poses": np.stack(poses) if poses else np.zeros((0, 4, 4)),
        "pose_index": np.asarray(index, dtype=np.int64),
        "pose_ok": np.asarray(ok, dtype=bool),
        "vertices": V,
        "faces": F,
    }
    for name in sorted(lasers):
        arrays[f"laser_{name}"] = np.concatenate([a.reshape(-1, 3) for a in lasers[name]]).astype(np.float32) \
            if lasers[name] else np.zeros((0, 3), np.float32)
    meta = {
        "quality": quality,
        "frames": int(report["frames"]["processed"]),
        "frames_requested": frames,
        "device": device,
        "open3d_threads": 1 if one_thread else (os.environ.get("OHS_O3D_THREADS", "").strip() or None),
        "compute": report.get("params", {}).get("compute"),
        "git_commit": _git_commit(Path(__file__).resolve().parent),
        "recording": str(rec),
        "seconds": round(seconds, 1),
    }
    return arrays, meta


def save_npz(path: Path, arrays: dict[str, np.ndarray], meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **arrays, **{META_KEY: np.array(json.dumps(meta, sort_keys=True))})


def run_snapshot(out: str | Path, frames: int | None = None, qualities: tuple[str, ...] = QUALITIES,
                 recording: str | Path | None = None, say=print) -> list[Path]:
    """Write ``<out>/<quality>.npz`` for each quality; returns the paths."""
    from ohs.app.bench import STANDARD, ensure_recording

    out = Path(out)
    rec = Path(recording) if recording else ensure_recording(STANDARD, echo=say)
    n = STANDARD.n_frames if frames is None and not recording else frames
    paths = []
    for q in qualities:
        say(f"{q}: processing {'all' if n is None else n} frames of {rec.name} ...")
        arrays, meta = take_snapshot(rec, q, n)
        p = out / f"{q}.npz"
        save_npz(p, arrays, meta)
        say(f"{q}: {meta['frames']} frames in {meta['seconds']} s on {meta['device']} -> {p}")
        paths.append(p)
    return paths


# --------------------------------------------------------------------------- compare
def load_npz(path: Path) -> tuple[dict[str, np.ndarray], dict]:
    with np.load(path, allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files if k != META_KEY}
        meta = json.loads(str(z[META_KEY])) if META_KEY in z.files else {}
    return arrays, meta


def max_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
    """Largest absolute difference of two same-shape arrays; NaN equals NaN, NaN against a number is inf."""
    if a.size == 0:
        return 0.0
    if a.dtype == bool or b.dtype == bool:
        return float(np.any(a != b))
    x, y = a.astype(np.float64), b.astype(np.float64)
    nx, ny = np.isnan(x), np.isnan(y)
    if (nx != ny).any():
        return float("inf")
    with np.errstate(invalid="ignore"):
        d = np.abs(x - y)
    d = np.where(nx & ny, 0.0, d)
    d = np.where(x == y, 0.0, d)  # equal infinities
    return float(d.max())


def compare_arrays(a: dict[str, np.ndarray], b: dict[str, np.ndarray], tol: float = 0.0,
                   label: str = "") -> tuple[list[str], bool]:
    """Report lines and whether everything matches (within ``tol``)."""
    lines: list[str] = []
    ok = True
    pre = f"{label}: " if label else ""
    for k in sorted(set(a) | set(b)):
        if k not in a or k not in b:
            lines.append(f"  {pre}{k}: only in {'b' if k not in a else 'a'}  DIFFERENT")
            ok = False
        elif a[k].shape != b[k].shape:
            lines.append(f"  {pre}{k}: shape {a[k].shape} vs {b[k].shape}  DIFFERENT")
            ok = False
        else:
            d = max_abs_diff(a[k], b[k])
            same = d <= tol
            ok &= same
            lines.append(f"  {pre}{k}: max abs diff {d:.9g}  {'ok' if same else 'DIFFERENT'}"
                         + ("" if d == 0 else " (identical not met)" if same else ""))
    return lines, ok


def _meta_warnings(ma: dict, mb: dict, label: str = "") -> list[str]:
    pre = f"{label}: " if label else ""
    out = []
    for key in ("device", "quality", "frames"):
        if ma.get(key) != mb.get(key):
            out.append(f"WARNING {pre}{key} differs: {ma.get(key)!r} vs {mb.get(key)!r}"
                       + (" (snapshots from different devices are not comparable bit for bit)" if key == "device" else ""))
    if not ma or not mb:
        out.append(f"WARNING {pre}no metadata in {'a' if not ma else 'b'}: device and commit unknown")
    if ma.get("git_commit") != mb.get("git_commit"):
        out.append(f"note {pre}git commit: {ma.get('git_commit')} vs {mb.get('git_commit')}")
    return out


def compare_paths(a: str | Path, b: str | Path, tol: float = 0.0, say=print) -> int:
    """Compare two ``.npz`` files or two folders of them; returns the exit code (0 = all match)."""
    a, b = Path(a), Path(b)
    if a.is_dir() != b.is_dir():
        say("a and b must both be .npz files or both be folders")
        return 2
    pairs: list[tuple[str, Path | None, Path | None]]
    if a.is_dir():
        names = sorted({p.name for p in a.glob("*.npz")} | {p.name for p in b.glob("*.npz")})
        if not names:
            say("no .npz files in either folder")
            return 2
        pairs = [(n, (a / n) if (a / n).is_file() else None, (b / n) if (b / n).is_file() else None) for n in names]
    else:
        pairs = [("", a, b)]
    code = 0
    for label, pa, pb in pairs:
        if pa is None or pb is None:
            say(f"{label}: only in {'b' if pa is None else 'a'}  DIFFERENT")
            code = 1
            continue
        for p in (pa, pb):
            if not p.is_file():
                say(f"not a file: {p}")
                return 2
        arrays_a, meta_a = load_npz(pa)
        arrays_b, meta_b = load_npz(pb)
        for w in _meta_warnings(meta_a, meta_b, label):
            say(w)
        lines, ok = compare_arrays(arrays_a, arrays_b, tol, label)
        for ln in lines:
            say(ln)
        if not ok:
            code = 1
    say(("IDENTICAL" if tol == 0 else f"MATCH within {tol:g}") if code == 0 else "DIFFERENT")
    return code


# --------------------------------------------------------------------------- command line
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="snapshot", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="process the bench recording and save <dir>/<quality>.npz")
    s.add_argument("dir", help="output folder")
    s.add_argument("--frames", type=int, default=None, help="frames to process (default: what ohs bench uses, 90)")
    s.add_argument("--qualities", nargs="+", choices=QUALITIES, default=list(QUALITIES))
    s.add_argument("--recording", default=None, help="another recording folder (default: the cached bench recording)")
    c = sub.add_parser("compare", help="compare two snapshots (files or folders); exit 0 only if identical")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--tol", type=float, default=0.0, help="largest absolute difference still accepted (default 0)")
    args = ap.parse_args(argv)
    if args.cmd == "snapshot":
        run_snapshot(args.dir, args.frames, tuple(args.qualities), args.recording)
        return 0
    return compare_paths(args.a, args.b, args.tol)


if __name__ == "__main__":
    sys.exit(main())
