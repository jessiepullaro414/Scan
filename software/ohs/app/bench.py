"""``ohs bench``: speed and accuracy of the processing pipeline on a standard simulated scan, CPU against GPU.

1. **The recording** (:data:`STANDARD`): ``sphere_on_plane``, 90 frames at 30 fps, 1280x800,
   realistic lens model, dots and both lasers, IMU, the true poses of every frame and the true
   depth of every 10th. It is simulated once (about 2 minutes) and kept under the bench folder
   (``~/.ohs/bench``; ``--dir`` or the ``OHS_BENCH_DIR`` environment variable move it). The folder
   name carries :data:`BENCH_VERSION` and a hash of the parameters, so changing either makes a
   new recording instead of reusing a stale one.
2. **The runs**: :class:`~ohs.app.pipeline.FramePipeline` over the frames for each device and each
   quality. Devices: ``"cpu"`` (``ohs.compute`` turned off for the run) and ``"gpu"`` (the
   configured device, ``compute.device``). Qualities: ``"full"`` (scale 1.0) and ``"preview"``
   (scale 0.5, 2 mm voxels), with the parameters of :func:`ohs.server.studio.process_params`, so
   the bench runs exactly what the app runs (built-in settings, not the user's config file, so
   results compare across machines). The IMU is fed as ``ohs process`` feeds it. A 3-frame
   warm-up pipeline runs first (GPU program builds, first allocations), so timings are steady.
3. **The report**:

   * speed: per stage of ``FrameResult.timings_ms`` the mean and median ms per frame, the total
     and frames per second, and the GPU speed-up per stage and overall. ``laser`` is
     ``laser_extract + laser_triangulate``, where ``laser_extract`` is how long the frame still waits
     for the laser lines (they are extracted in two threads during the depth; ``laser_extract_cpu``
     is their own time, both images together); ``track`` includes ``track_filter``, ``raycast``,
     ``icp`` and the integrations;
   * the wait per minute of scanning: minutes to process 60 s of recording at the camera's frame
     rate (``CameraParams.fps``), from the mean total frame time, assuming every frame is processed;
   * the fixed tail after the last frame (``tail_ms`` of a run): mesh extraction, cleaning, each export
     format and the laser points file, timed one by one as :func:`_tail`; it grows with the scan;
   * the engine start (``engine_start`` of the report, once): :func:`measure_engine_start`, a new
     ``ohs app`` process until its first status message;
   * accuracy: the trajectory error against the true poses
     (:func:`~ohs.app.process.trajectory_errors`) and the depth error on the frames with true depth
     (median, p95, share of pixels off by more than 2 mm, bias, valid share overall and where dots
     are visible);
   * the machine: GPU (:func:`ohs.compute.describe`), CPU model and logical cores, OS, Python and
     package versions, git commit (read from ``.git``, if the code runs from a checkout).

The table goes to the console; the whole result is saved as JSON under
``<bench folder>/results/bench-<time>.json`` (and ``--json PATH``), so runs on different machines
can be compared. Without a GPU the CPU part runs and the report says why there is no GPU.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from ohs.devices.camera import CameraParams

__all__ = ["BENCH_VERSION", "BenchSpec", "STANDARD", "QUALITIES", "DEVICES", "CAMERA_FPS", "default_bench_dir",
           "ensure_recording", "wait_per_minute_min", "measure_engine_start", "run_bench", "format_report",
           "machine_info", "save_report"]

log = logging.getLogger(__name__)

BENCH_VERSION = 1
QUALITIES = ("full", "preview")
DEVICES = ("cpu", "gpu")
ENV_DIR = "OHS_BENCH_DIR"
WARMUP_FRAMES = 3
OUTLIER_MM = 2.0
CAMERA_FPS = float(CameraParams().fps)  # the camera's frame rate (60); the scan-wait figure assumes every frame is processed
# stage order of the table (others follow in the order the pipeline reports them)
_TAIL_LABELS = {"mesh": "mesh extraction", "poisson": "Poisson meshing", "clean": "cleaning",
                "laser_points": "laser points file"}
STAGE_ORDER = ("rectify", "depth", "laser_extract", "laser_triangulate", "laser", "track_filter", "raycast", "icp",
               "track_integrate", "integrate", "track", "total")


@dataclass(frozen=True)
class BenchSpec:
    """The simulated bench recording (:func:`ohs.sim.datasets.make_scan_recording` arguments)."""

    scene: str = "sphere_on_plane"
    n_frames: int = 90
    fps: float = 30.0
    image_size: tuple[int, int] = (1280, 800)
    realistic: bool = True
    depth_every: int = 10
    seed: int = 0

    def key(self) -> str:
        """Short hash of the parameters and :data:`BENCH_VERSION`."""
        blob = json.dumps({"version": BENCH_VERSION, **asdict(self)}, sort_keys=True)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]

    def folder_name(self) -> str:
        w, h = self.image_size
        return f"rec-v{BENCH_VERSION}-{self.scene}-{w}x{h}-{self.n_frames}f-{self.key()}"

    def describe(self) -> str:
        w, h = self.image_size
        return (f"{self.scene}, {self.n_frames} frames at {self.fps:g} fps, {w}x{h}"
                f"{', realistic lens' if self.realistic else ''}, true depth every {self.depth_every}th frame")


STANDARD = BenchSpec()


def default_bench_dir() -> Path:
    """``$OHS_BENCH_DIR``, else ``~/.ohs/bench``."""
    env = os.environ.get(ENV_DIR)
    return Path(env).expanduser() if env else Path.home() / ".ohs" / "bench"


# --------------------------------------------------------------------------- the recording
def ensure_recording(spec: BenchSpec | None = None, bench_dir: str | Path | None = None,
                     echo: Callable[[str], None] | None = None) -> Path:
    """The cached bench recording for ``spec`` (default :data:`STANDARD`); simulated first if
    missing or incomplete."""
    from ohs.sim.datasets import make_scan_recording

    spec = spec if spec is not None else STANDARD

    root = Path(bench_dir).expanduser() if bench_dir is not None else default_bench_dir()
    path = root / spec.folder_name()
    marker = path / "bench_spec.json"
    want = {"bench_version": BENCH_VERSION, "spec": asdict(spec)}
    if (path / "frames.csv").is_file() and marker.is_file():
        try:
            if json.loads(marker.read_text(encoding="utf-8")) == json.loads(json.dumps(want)):
                return path
        except (OSError, ValueError):
            pass
    root.mkdir(parents=True, exist_ok=True)
    w, h = spec.image_size
    est = spec.n_frames * w * h / (1280 * 800) * 1.3
    if echo:
        echo(f"Simulating the bench recording once ({spec.describe()}; about {max(est, 5):.0f} s) into {path}")
    tmp = root / f"{path.name}.tmp-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    t0 = time.perf_counter()
    make_scan_recording(tmp, scene=spec.scene, n_frames=spec.n_frames, fps=spec.fps, image_size=tuple(spec.image_size),
                        seed=spec.seed, realistic=spec.realistic, depth_every=spec.depth_every)
    (tmp / "bench_spec.json").write_text(json.dumps(want, indent=1), encoding="utf-8")
    shutil.rmtree(path, ignore_errors=True)
    tmp.rename(path)
    log.info("bench recording ready in %.0f s: %s", time.perf_counter() - t0, path)
    return path


# --------------------------------------------------------------------------- machine
def _cpu_name() -> str:
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        if sys.platform.startswith("linux"):
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        if sys.platform == "darwin":
            import subprocess

            out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
    except Exception:  # pragma: no cover - best effort
        pass
    return platform.processor() or platform.machine() or "unknown"


def _git_commit(start: Path) -> str | None:
    """The checked-out commit, read from ``.git`` (no git executable needed); ``None`` if not a checkout."""
    try:
        for d in (start, *start.parents):
            g = d / ".git"
            if g.is_file():  # worktree or submodule: "gitdir: <path>"
                text = g.read_text(encoding="utf-8").strip()
                if not text.startswith("gitdir:"):
                    return None
                g = (d / text[len("gitdir:"):].strip()).resolve()
            if not g.is_dir():
                continue
            head = (g / "HEAD").read_text(encoding="utf-8").strip()
            if not head.startswith("ref:"):
                return head[:12] or None
            ref = head[len("ref:"):].strip()
            common = g
            if (g / "commondir").is_file():
                common = (g / (g / "commondir").read_text(encoding="utf-8").strip()).resolve()
            for base in (g, common):
                f = base / ref
                if f.is_file():
                    return f.read_text(encoding="utf-8").strip()[:12] or None
            packed = common / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0][:12]
            return None
    except OSError:
        return None
    return None


def machine_info() -> dict:
    """CPU, OS, Python, package versions and git commit of this installation."""
    from ohs import __version__

    pkgs: dict[str, str | None] = {"ohs": __version__, "numpy": np.__version__, "opencv": cv2.__version__}
    for name, attr in (("open3d", "__version__"), ("pyopencl", "VERSION_TEXT")):
        try:
            mod = __import__(name)
            pkgs[name] = str(getattr(mod, attr, None))
        except Exception:
            pkgs[name] = None
    return {
        "cpu": _cpu_name(),
        "logical_cpus": os.cpu_count(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "packages": pkgs,
        "git_commit": _git_commit(Path(__file__).resolve().parent),
        "hostname": platform.node(),
    }


# --------------------------------------------------------------------------- one run
def _gt_at(gt: np.ndarray, shape: tuple[int, int]) -> np.ndarray | None:
    """True depth on the processed grid: the same size, or the 2x2-block average (inverse depth) at
    half size (only where the block is one surface)."""
    if gt.shape == shape:
        return gt.astype(np.float32)
    h, w = shape
    if gt.shape != (2 * h, 2 * w):
        return None
    b = gt.reshape(h, 2, w, 2).transpose(0, 2, 1, 3).reshape(h, w, 4).astype(np.float64)
    ok = (b > 0).all(-1)
    inv = np.where(b > 0, 1.0 / np.maximum(b, 1e-9), 0.0)
    m = inv.mean(-1)
    ok &= (inv.max(-1) - inv.min(-1)) <= 0.01 * np.maximum(m, 1e-12)
    return np.where(ok, 1.0 / np.maximum(m, 1e-12), 0.0).astype(np.float32)


class _DepthErrors:
    """Pooled depth errors of the frames with true depth (pixels in range with a correspondence)."""

    def __init__(self, zmin: float, zmax: float) -> None:
        self.zmin, self.zmax = float(zmin), float(zmax)
        self.err: list[np.ndarray] = []
        self.n_eval = self.n_valid = self.n_lit = self.n_lit_valid = self.frames = 0

    def add(self, depth: np.ndarray, gt_full: np.ndarray, left_r: np.ndarray, K: np.ndarray, baseline: float) -> None:
        gt = _gt_at(gt_full, depth.shape)
        if gt is None:
            return
        h, w = gt.shape
        f = float(K[0, 0])
        m = (gt >= self.zmin) & (gt <= self.zmax)
        m &= (np.arange(w)[None, :] - f * baseline / np.maximum(gt, 1e-6)) >= 4
        b = 4
        m[:b], m[-b:], m[:, :b], m[:, -b:] = False, False, False, False
        k = max(3, int(round(7 * w / 640)) | 1)
        lit = (cv2.dilate(left_r, np.ones((k, k), np.uint8)).astype(np.int16)
               - cv2.medianBlur(left_r, k).astype(np.int16)) > 25
        sel = m & (depth > 0)
        self.err.append((depth - gt)[sel].astype(np.float32))
        self.n_eval += int(m.sum())
        self.n_valid += int(sel.sum())
        self.n_lit += int((m & lit).sum())
        self.n_lit_valid += int((sel & lit).sum())
        self.frames += 1

    def summary(self) -> dict:
        if not self.frames or not self.n_eval:
            return {"frames": self.frames}
        e = np.concatenate(self.err) if self.err else np.zeros(0, np.float32)
        a = np.abs(e)
        out: dict[str, Any] = {"frames": self.frames, "pixels": self.n_eval,
                               "valid_fraction": self.n_valid / self.n_eval,
                               "valid_fraction_lit": self.n_lit_valid / self.n_lit if self.n_lit else None}
        if a.size:
            out.update(median_mm=float(np.median(a)), p95_mm=float(np.percentile(a, 95)),
                       outlier_fraction=float(np.mean(a > OUTLIER_MM)), bias_mm=float(np.median(e)))
        return out


def wait_per_minute_min(frame_ms: float, scan_fps: float = CAMERA_FPS) -> float:
    """Minutes of processing for one minute of recording: ``60 * scan_fps`` frames at ``frame_ms`` each."""
    return 60.0 * float(scan_fps) * float(frame_ms) / 1000.0 / 60.0


def _stats(values: list[float]) -> dict:
    a = np.asarray(values, dtype=np.float64)
    return {"mean": float(a.mean()), "median": float(np.median(a)), "max": float(a.max()), "n": int(a.size)}


def _run(rec: Path, quality: str, device: str, n_frames: int, progress: Callable[[int, int], None] | None) -> dict:
    """Process the first ``n_frames`` frames at ``quality`` on the current compute setting."""
    from ohs.app.config import Config
    from ohs.app.pipeline import FramePipeline
    from ohs.app.process import IMU_LOOKAHEAD_S, trajectory_errors
    from ohs.io.recording import RecordingReader
    from ohs.server.studio import process_params

    reader = RecordingReader(rec)
    calib = reader.calibration()
    width = int(calib.stereo.image_size[0])
    params = process_params(Config(), "markerless", quality, width)
    pp = params.pipeline
    imu = reader.imu_arrays()
    order = np.argsort(imu["t_host"], kind="stable")
    imu_t, imu_g = imu["t_host"][order], imu["gyro"][order]
    offset = float(calib.imu.time_offset) if calib.imu is not None else 0.0

    def feed(pipe: Any, t_frame: float, j: int) -> int:
        if pipe.gyro_enabled and j < len(imu_t):
            j_new = int(np.searchsorted(imu_t, t_frame - offset + IMU_LOOKAHEAD_S, side="right"))
            if j_new > j:
                pipe.add_imu_arrays(imu_t[j:j_new], imu_g[j:j_new])
                return j_new
        return j

    with FramePipeline(calib, pp) as warm:  # builds the GPU programs; its results are not used
        j = 0
        for i in range(min(WARMUP_FRAMES, n_frames)):
            fr = reader.frame(i)
            j = feed(warm, fr.t_host, j)
            warm.process(fr)

    pipe = FramePipeline(calib, pp)
    try:
        est = pipe.depth_estimator
        info: dict[str, Any] = {
            "device": device, "quality": quality, "scale": float(pp.scale), "image_size": list(pipe.image_size),
            "voxel_mm": float(pipe.fusion_params.voxel_mm),
            "depth_backend": getattr(est, "backend", "cpu"), "depth_device": getattr(est, "device", None),
            "tracking_backend": getattr(pipe, "model_backend", None) or getattr(pipe.tracker, "backend", None),
        }
        dp = pipe.depth_params
        derr = _DepthErrors(dp.min_depth_mm, dp.max_depth_mm)
        timings: dict[str, list[float]] = {}
        traj: list[tuple[int, np.ndarray, bool]] = []
        j = 0
        t0 = time.perf_counter()
        for k in range(n_frames):
            fr = reader.frame(k)
            j = feed(pipe, fr.t_host, j)
            res = pipe.process(fr)
            for key, v in res.timings_ms.items():
                timings.setdefault(key, []).append(float(v))
            traj.append((res.index, np.asarray(res.track.T_wc, dtype=np.float64), bool(res.track.ok)))
            gt = reader.ground_truth_depth(int(res.index))
            if gt is not None and res.depth is not None:
                derr.add(res.depth, gt, res.left_r, pipe.K, pipe.baseline)
            if progress is not None:
                progress(k + 1, n_frames)
        wall = time.perf_counter() - t0
        tail = _tail(pipe, params)
    finally:
        pipe.close()
    tstats = {key: _stats(v) for key, v in timings.items()}
    total = tstats.get("total", {}).get("mean")
    te = trajectory_errors(reader.ground_truth_poses() or {}, traj)
    te.pop("per_frame", None)
    info.update(frames=n_frames, tracked=int(sum(ok for _, _, ok in traj)), timings_ms=tstats,
                fps=(1000.0 / total) if total else None, wall_s=wall, trajectory=te, depth_error=derr.summary(),
                scan_fps=CAMERA_FPS, wait_per_minute_min=wait_per_minute_min(total) if total else None,
                tail_ms=tail)
    return info


def _tail(pipe: Any, params: Any) -> dict:
    """The fixed tail after the last frame: the steps :func:`ohs.app.process.process_recording` runs
    between the last frame and the returned mesh files (process.py "# mesh" and "# laser cloud"),
    timed one by one in ms, in the same order and with the same arguments, files written to a
    temporary folder: ``mesh`` (``pipe.mesh_with_sigma``), ``poisson`` (only if the quality asks for it),
    ``clean``, ``export_<ext>`` per format and ``laser_points`` (the laser cloud PLY), plus their
    sum ``total``. Not covered: the report statistics and ``report.json``, ``trajectory.csv`` and the
    app's own step after that (reading the mesh back into the project, ``server/processing.py``
    ``_new_base``). A step that fails is reported as ``error`` instead of stopping the bench."""
    import tempfile

    steps: dict[str, float] = {}
    faces: int | None = None

    def timed(name: str, fn: Callable[[], Any]) -> Any:
        t = time.perf_counter()
        try:
            return fn()
        finally:
            steps[name] = 1e3 * (time.perf_counter() - t)

    try:
        from ohs.mesh.confidence import SIGMA_NONE_MM
        from ohs.track.export import clean_mesh, poisson_mesh, save_mesh

        V, F, N, S = timed("mesh", pipe.mesh_with_sigma)
        if params.poisson:
            def poisson() -> tuple:
                P, Np = pipe.points()
                if len(P) >= 10:
                    return poisson_mesh(P, Np, depth=int(params.poisson_depth)) + (None,)
                return V, F, N, S

            V, F, N, S = timed("poisson", poisson)

        def clean() -> tuple:
            v, f, n, s = V, F, N, S
            if params.clean_min_component > 0 and len(f):
                v, f, idx = clean_mesh(v, f, min_component_fraction=float(params.clean_min_component), return_index=True)
                n = n[idx] if n is not None and len(n) else n
                s = s[idx] if s is not None and len(s) else s
            if s is not None:
                s = np.minimum(s, np.float32(SIGMA_NONE_MM)).astype(np.float32)
            return v, f, n, s

        V, F, N, S = timed("clean", clean)
        faces = int(len(F))
        with tempfile.TemporaryDirectory(prefix="ohs-bench-tail-") as tmp:
            for ext in params.export:
                timed(f"export_{ext}", lambda ext=ext: save_mesh(
                    Path(tmp) / f"mesh.{ext}", V, F, N if ext != "stl" and N is not None and len(N) == len(V) else None,
                    quality=S if ext == "ply" and S is not None and len(S) == len(V) else None))
            timed("laser_points", lambda: pipe.laser_cloud().save_ply(
                Path(tmp) / "laser_points.ply", voxel_mm=float(params.laser_voxel_mm)))
        out: dict[str, Any] = dict(steps)
    except Exception as e:  # report what was timed, not a failed bench
        log.warning("bench tail: a step failed: %s", e)
        out = {**steps, "error": f"{type(e).__name__}: {e}"}
    out["total"] = float(sum(steps.values()))
    out["faces"] = faces
    return out


# --------------------------------------------------------------------------- engine start
def measure_engine_start(timeout: float = 90.0, python: str | None = None) -> dict:
    """Seconds from launching ``ohs app --no-window`` (a fresh process, like the user's) to its first
    status message on the live WebSocket (``/api/ws/live``, ``{"type": "status"}``, sent 10 times a second
    to connected clients, ``server/studio.py`` ``_status_loop``).

    The process runs with built-in settings (``OHS_CONFIG`` points to no file) and an empty home folder
    for projects, on a free local port, and is stopped afterwards; the port is asked about every 60 ms,
    which is the resolution of ``listening_s``. Returns ``{"ok": True, "listening_s",
    "first_status_s", "port"}``: ``listening_s`` is when the WebSocket first accepted a connection (the
    server is up and its startup ran); or ``{"ok": False, "error": ...}``. Never raises."""
    import socket
    import subprocess
    import tempfile

    try:
        from websockets.sync.client import connect
    except ImportError as e:
        return {"ok": False, "error": f"the websockets package is missing ({e})"}
    code = "import sys; from ohs.app.cli import main; sys.exit(main(sys.argv[1:]))"

    def first_status(proc: Any, port: int, t0: float) -> dict:
        while True:  # until the port accepts a connection
            if proc.poll() is not None:
                return {"ok": False, "error": f"ohs app exited with code {proc.returncode} before it was up"}
            if time.perf_counter() - t0 > timeout:
                return {"ok": False, "error": f"no connection to the app within {timeout:.0f} s"}
            try:  # Windows answers an attempt on a closed local port only after 2 s: give up after 50 ms and ask again
                socket.create_connection(("127.0.0.1", port), timeout=0.05).close()
                break
            except OSError:
                time.sleep(0.01)
        listening = time.perf_counter() - t0
        with connect(f"ws://127.0.0.1:{port}/api/ws/live", open_timeout=max(1.0, timeout - listening),
                     close_timeout=1) as ws:
            while True:
                left = timeout - (time.perf_counter() - t0)
                if left <= 0:
                    return {"ok": False, "error": f"no status message within {timeout:.0f} s"}
                msg = ws.recv(timeout=left)
                if isinstance(msg, str) and '"status"' in msg and json.loads(msg).get("type") == "status":
                    return {"ok": True, "listening_s": listening, "first_status_s": time.perf_counter() - t0,
                            "port": port}

    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix="ohs-bench-start-", ignore_cleanup_errors=True) as home:
            env = {**os.environ, "OHS_CONFIG": str(Path(home) / "no-config.toml"), "USERPROFILE": home, "HOME": home}
            t0 = time.perf_counter()
            proc = subprocess.Popen([python or sys.executable, "-c", code, "app", "--no-window", "--port", str(port)],
                                    env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            try:
                return first_status(proc, port, t0)
            finally:  # stop the app before its home folder is removed
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(10)
                    except Exception:
                        proc.kill()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# --------------------------------------------------------------------------- all runs
def run_bench(frames: int | None = None, qualities: tuple[str, ...] = QUALITIES, devices: tuple[str, ...] = DEVICES,
              bench_dir: str | Path | None = None, spec: BenchSpec | None = None, compute_device: str = "auto",
              echo: Callable[[str], None] | None = None,
              progress: Callable[[str, int, int], None] | None = None, engine_start: bool = True) -> dict:
    """Run the bench and return the report (see the module docstring); nothing is saved.

    ``compute_device`` is the GPU preference of the GPU runs (``compute.device``; ``"cpu"`` there
    means "auto" here) and is restored afterwards. The CPU runs set ``OHS_COMPUTE=cpu`` for their
    duration, so nothing runs on the GPU even when the environment chooses one.
    """
    from ohs import compute

    spec = spec if spec is not None else STANDARD
    for q in qualities:
        if q not in QUALITIES:
            raise ValueError(f"unknown quality {q!r}; choose from {QUALITIES}")
    for d in devices:
        if d not in DEVICES:
            raise ValueError(f"unknown device {d!r}; choose from {DEVICES}")
    say = echo or (lambda s: None)
    root = Path(bench_dir).expanduser() if bench_dir is not None else default_bench_dir()
    rec = ensure_recording(spec, root, echo=say)
    n = spec.n_frames if frames is None else max(1, min(int(frames), spec.n_frames))
    gpu_pref = compute_device if compute_device and compute_device.strip().lower() != "cpu" else "auto"
    report: dict[str, Any] = {
        "bench_version": BENCH_VERSION,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recording": {"path": str(rec), "spec": asdict(spec), "frames_used": n},
        "machine": machine_info(),
        "runs": [],
    }
    if engine_start:  # once, before the runs, while nothing else of the bench is running
        say("Engine start: launching the app once")
        report["engine_start"] = measure_engine_start()
    env_before = os.environ.get(compute.ENV_VAR)
    try:
        for device in DEVICES:
            if device not in devices:
                continue
            if device == "cpu":
                os.environ[compute.ENV_VAR] = "cpu"
                compute.reset()
            else:
                if env_before is None:
                    os.environ.pop(compute.ENV_VAR, None)
                else:
                    os.environ[compute.ENV_VAR] = env_before
                compute.configure(gpu_pref)
                compute.reset()
                desc = compute.describe()
                report["gpu"] = desc
                if desc["device"] is None:
                    report["no_gpu_reason"] = desc["status"]
                    say(f"No GPU run: {desc['status']}")
                    continue
                say(f"GPU: {desc['device']['name']} ({desc['device']['kind']}, {desc['device']['memory_mb']} MB, "
                    f"{desc['device']['opencl']})")
            for quality in QUALITIES:
                if quality not in qualities:
                    continue
                label = f"{device.upper()}, {quality} quality"
                say(f"{label}: {n} frames")
                cb = (lambda k, total, _l=label: progress(_l, k, total)) if progress else None
                report["runs"].append(_run(rec, quality, device, n, cb))
    finally:
        if env_before is None:
            os.environ.pop(compute.ENV_VAR, None)
        else:
            os.environ[compute.ENV_VAR] = env_before
        compute.configure(compute_device)
        compute.reset()
    report["speedup"] = _speedups(report["runs"])
    return report


def _speedups(runs: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for q in QUALITIES:
        c = next((r for r in runs if r["quality"] == q and r["device"] == "cpu"), None)
        g = next((r for r in runs if r["quality"] == q and r["device"] == "gpu"), None)
        if c is None or g is None:
            continue
        sp = {}
        for stage, cs in c["timings_ms"].items():
            gs = g["timings_ms"].get(stage)
            if gs and gs["mean"] > 0:
                sp[stage] = cs["mean"] / gs["mean"]
        out[q] = sp
    return out


# --------------------------------------------------------------------------- output
def _stages(runs: list[dict]) -> list[str]:
    keys: list[str] = []
    for r in runs:
        for k in r["timings_ms"]:
            if k not in keys:
                keys.append(k)
    known = [k for k in STAGE_ORDER if k in keys and k != "total"]
    rest = [k for k in keys if k not in STAGE_ORDER]
    return known + rest + (["total"] if "total" in keys else [])


def format_report(report: dict) -> str:
    """The console table of a :func:`run_bench` report."""
    m = report["machine"]
    lines = [f"OpenHandScan bench v{report['bench_version']}: {BenchSpec(**{**report['recording']['spec'], 'image_size': tuple(report['recording']['spec']['image_size'])}).describe()}"
             f" ({report['recording']['frames_used']} frames used)",
             f"CPU: {m['cpu']} ({m['logical_cpus']} logical cores); {m['os']}; Python {m['python']}"]
    pk = m["packages"]
    lines.append("Software: " + ", ".join(f"{k} {v}" for k, v in pk.items() if v) + (f"; commit {m['git_commit']}" if m.get("git_commit") else ""))
    g = report.get("gpu")
    if g and g.get("device"):
        d = g["device"]
        lines.append(f"GPU: {d['name']} ({d['vendor']}, {d['kind']}, {d['memory_mb']} MB, {d['opencl']})")
    elif "no_gpu_reason" in report:
        lines.append(f"GPU: none. {report['no_gpu_reason']}")
    es = report.get("engine_start")
    if es:
        if es.get("ok"):
            lines.append(f"Engine start (ohs app in a new process): {es['listening_s']:.2f} s until the app accepts a "
                         f"connection, {es['first_status_s']:.2f} s until its first status message")
        else:
            lines.append(f"Engine start: not measured. {es.get('error', '')}")
    for q in QUALITIES:
        runs = [r for r in report["runs"] if r["quality"] == q]
        if not runs:
            continue
        w, h = runs[0]["image_size"]
        lines.append("")
        lines.append(f"{q.capitalize()} quality ({w}x{h}, {runs[0]['voxel_mm']:g} mm voxels): ms per frame, mean / median")
        cols = [r["device"] for r in runs]
        head = f"  {'stage':<18}" + "".join(f"{c.upper():>18}" for c in cols)
        sp = report.get("speedup", {}).get(q)
        if sp:
            head += f"{'GPU speed-up':>15}"
        lines.append(head)
        for stage in _stages(runs):
            row = f"  {stage:<18}"
            for r in runs:
                s = r["timings_ms"].get(stage)
                row += f"{s['mean']:>9.1f} /{s['median']:>7.1f}" if s else f"{'-':>18}"
            if sp:
                row += f"{sp[stage]:>14.2f}x" if stage in sp else f"{'':>15}"
            lines.append(row)
        lines.append(f"  {'frames per second':<18}" + "".join(f"{r['fps']:>18.2f}" if r.get("fps") else f"{'-':>18}" for r in runs))
        for r in runs:
            if r.get("wait_per_minute_min") is not None:
                lines.append(f"  {r['device'].upper()}: wait per minute of scanning at {r.get('scan_fps', CAMERA_FPS):g} fps: "
                             f"{r['wait_per_minute_min']:.1f} min (mean frame time, every frame processed)")
        for r in runs:
            tl = r.get("tail_ms")
            if tl:
                lines.append(f"  {r['device'].upper()}: fixed tail after the last frame: {tl['total'] / 1000:.2f} s "
                             f"(mesh extraction to files, after {r['frames']} frames, {tl.get('faces')} faces; "
                             f"grows with the scan)")
                for k, v in tl.items():
                    if k not in ("total", "faces", "error"):
                        lines.append(f"      {_TAIL_LABELS.get(k) or k.replace('_', ' ')}: {v / 1000:.2f} s")
                if tl.get("error"):
                    lines.append(f"      tail incomplete: {tl['error']}")
        for r in runs:
            te, de = r["trajectory"], r["depth_error"]
            who = f"{r['device'].upper()} (depth on {r['depth_backend'].upper()}"
            who += f", tracking model {r['tracking_backend']})" if r.get("tracking_backend") else ")"
            if te.get("n"):
                lines.append(f"  {who}: {r['tracked']}/{r['frames']} frames tracked; trajectory error median "
                             f"{te['trans_mm']['median']:.2f} mm / {te['rot_deg']['median']:.3f} deg, max "
                             f"{te['trans_mm']['max']:.2f} mm / {te['rot_deg']['max']:.3f} deg")
            else:
                lines.append(f"  {who}: {r['tracked']}/{r['frames']} frames tracked; no trajectory error (nothing tracked)")
            if de.get("median_mm") is not None:
                lit = de.get("valid_fraction_lit")
                lines.append(f"      depth vs truth ({de['frames']} frames): median {de['median_mm']:.3f} mm, p95 "
                             f"{de['p95_mm']:.2f} mm, off by > {OUTLIER_MM:g} mm {100 * de['outlier_fraction']:.1f} %, bias "
                             f"{de['bias_mm']:+.3f} mm, valid {100 * de['valid_fraction']:.1f} %"
                             + (f" ({100 * lit:.1f} % where dots are visible)" if lit is not None else ""))
    total = {q: s.get("total") for q, s in report.get("speedup", {}).items() if s.get("total")}
    if total:
        lines.append("")
        lines.append("Overall GPU speed-up: " + ", ".join(f"{q} {v:.2f}x" for q, v in total.items()))
    return "\n".join(lines)


def save_report(report: dict, bench_dir: str | Path | None = None, extra: str | Path | None = None) -> list[Path]:
    """Write the report to ``<bench dir>/results/bench-<time>.json`` (and ``extra``); returns the paths."""
    from ohs.app.process import _jsonable

    root = Path(bench_dir).expanduser() if bench_dir is not None else default_bench_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    paths = [root / "results" / f"bench-{stamp}.json"]
    if extra:
        paths.append(Path(extra).expanduser())
    text = json.dumps(_jsonable(report), indent=1)
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return paths
