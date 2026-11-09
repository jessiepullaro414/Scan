"""The simulator demo recording behind the ``sim`` device source.

The demo is an ordinary recording folder written by :func:`ohs.sim.datasets.make_scan_recording`
(sphere on a plane, 1280x800, 72 frames at 30 fps by default, the true calibration in
``calibration.json``). It is generated once into ``<demo_dir>/sim-demo/`` (default
``~/.ohs/demo/``) and replayed in a loop by :class:`~ohs.devices.camera.ReplayCamera`.

**Seamless loop.** A plain :class:`~ohs.sim.trajectory.HandheldTrajectory` ends somewhere else
than it starts, so a looped replay would "teleport" the scanner back at every pass and the live
tracker would lose the model. :class:`PeriodicSweep` time-warps the hand-held trajectory with a
cosine, ``s(t) = sweep * (1 - cos(2 pi t / period)) / 2``: the scanner sweeps forward, slows down,
sweeps back and arrives at its start pose exactly one recording length later. The warp is smooth,
so the synthesised IMU (finite differences of the trajectory) stays physical, and each loop pass
continues the motion of the previous one. ``sweep`` is chosen so the peak azimuth rate is about
30 deg/s, similar to the validated scans (VALIDATION.md).

**Cache validity.** Generation writes into a temporary sibling folder and renames it at the end,
then writes ``demo.json`` last, so an interrupted generation never looks complete. Any folder with
``frames.csv``, ``calibration.json``, ``meta.json`` and ``demo.json`` counts as a valid demo, whatever
its size: tests pre-generate a tiny one with :func:`generate_demo`.
"""
from __future__ import annotations

import json
import logging
import math
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

__all__ = [
    "DEMO_NAME",
    "DEMO_VERSION",
    "DEFAULT_DEMO",
    "DemoParams",
    "PeriodicSweep",
    "default_demo_dir",
    "demo_path",
    "demo_ready",
    "generate_demo",
]

log = logging.getLogger(__name__)

DEMO_NAME = "sim-demo"
DEMO_VERSION = 1
PEAK_SWEEP_DEG_S = 30.0  # peak azimuth rate of the back-and-forth sweep


@dataclass(frozen=True)
class DemoParams:
    """What the demo recording looks like (the defaults are the shipped demo)."""

    scene: str = "sphere_on_plane"
    n_frames: int = 72
    image_size: tuple[int, int] = (1280, 800)
    fps: float = 30.0
    seed: int = 0

    @property
    def period_s(self) -> float:
        return self.n_frames / self.fps

    def estimate_s(self) -> float:
        """Rough generation time: about 1 s per 1280x800 stereo frame (VALIDATION.md)."""
        w, h = self.image_size
        return 2.0 + self.n_frames * (w * h) / (1280 * 800)


DEFAULT_DEMO = DemoParams()


def default_demo_dir() -> Path:
    return Path("~/.ohs/demo").expanduser()


def demo_path(demo_dir: str | Path) -> Path:
    """The demo recording folder inside ``demo_dir``."""
    return Path(demo_dir).expanduser() / DEMO_NAME


def demo_ready(path: str | Path) -> bool:
    """True if ``path`` holds a complete demo recording (see the module docstring)."""
    p = Path(path)
    need = ("frames.csv", "calibration.json", "meta.json", "demo.json")
    if not all((p / n).is_file() for n in need):
        return False
    try:
        meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        info = json.loads((p / "demo.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return int(meta.get("n_frames", 0)) > 0 and int(info.get("version", 0)) == DEMO_VERSION


@dataclass
class PeriodicSweep:
    """``base`` time-warped into a smooth back-and-forth loop of ``period`` seconds.

    ``pose(t_start + k * period) == base.pose(t_start)`` for every integer ``k``; between, the
    base trajectory is traversed from ``t_start`` to ``t_start + sweep`` and back (cosine warp).
    """

    base: object  # any Trajectory: pose(t), poses(t)
    t_start: float
    period: float
    sweep: float

    def warp(self, t: np.ndarray) -> np.ndarray:
        t = np.atleast_1d(np.asarray(t, dtype=np.float64))
        phase = 2.0 * math.pi * (t - self.t_start) / self.period
        return self.t_start + self.sweep * 0.5 * (1.0 - np.cos(phase))

    def poses(self, t: np.ndarray) -> np.ndarray:
        return self.base.poses(self.warp(t))  # type: ignore[attr-defined]

    def pose(self, t: float) -> np.ndarray:
        return self.poses(np.array([float(t)]))[0]

    def __repr__(self) -> str:
        return f"PeriodicSweep(period={self.period:.3f} s, sweep={self.sweep:.3f} s of {self.base!r})"


def _trajectory(params: DemoParams):
    from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration
    from ohs.sim.scene import make_scene
    from ohs.sim.trajectory import HandheldTrajectory

    calib = nominal_calibration(params.image_size)
    sc = make_scene(params.scene, params.seed)
    kw = dict(sc.meta.get("trajectory", {}))
    sweep_rate = float(kw.get("sweep_deg_s", 20.0))
    # make_scan_recording: first frame at camera time t0 + time_offset (t0 = 100 s)
    t_start = 100.0 + NOMINAL_TIME_OFFSET
    period = params.period_s
    # peak base-time rate of the warp is sweep * pi / period
    sweep = PEAK_SWEEP_DEG_S / max(sweep_rate, 1e-6) * period / math.pi
    base = HandheldTrajectory(target=sc.target, t0=t_start, duration=max(sweep, 1e-3), seed=params.seed,
                              baseline=calib.stereo.baseline, **kw)
    return PeriodicSweep(base, t_start, period, sweep)


def _count_frames(folder: Path) -> int:
    try:
        return sum(1 for _ in (folder / "frames").glob("*.png"))
    except OSError:
        return 0


def generate_demo(
    out_path: str | Path,
    params: DemoParams = DEFAULT_DEMO,
    *,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Render the demo recording into ``out_path`` (replaced if it exists but is incomplete).

    ``progress(fraction, message)`` is called about twice a second (the fraction counts the
    rendered frames). ``cancelled()`` is polled at the same rate: the renderer cannot be
    interrupted mid-way, so on cancel this function returns at once by raising
    ``InterruptedError`` and the render thread discards its output when it finishes.
    """
    from ohs.sim.datasets import make_scan_recording

    out = Path(out_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    for stale in out.parent.glob(f"{out.name}.partial-*"):  # left by an app that was closed mid-way
        try:
            if time.time() - stale.stat().st_mtime > 600:
                shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            pass
    tmp = out.with_name(f"{out.name}.partial-{uuid.uuid4().hex[:8]}")
    traj = _trajectory(params)
    state: dict = {"error": None, "done": False, "abandoned": False}
    lock = threading.Lock()

    def render() -> None:
        try:
            make_scan_recording(tmp, scene=params.scene, n_frames=int(params.n_frames), fps=float(params.fps),
                                image_size=tuple(params.image_size), seed=int(params.seed), trajectory=traj,
                                depth_every=0)
            (tmp / "demo.json").write_text(json.dumps({
                "version": DEMO_VERSION, "scene": params.scene, "n_frames": int(params.n_frames),
                "image_size": list(params.image_size), "fps": float(params.fps), "seed": int(params.seed),
                "loop": "seamless back-and-forth sweep, period = recording length", "trajectory": repr(traj),
            }, indent=1), encoding="utf-8")
        except BaseException as e:  # reported to the caller
            state["error"] = e
        finally:
            with lock:
                state["done"] = True
                abandoned = state["abandoned"]
            if abandoned or state["error"] is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    t0 = time.perf_counter()
    log.info("generating the simulator demo (%d frames, %dx%d) in %s", params.n_frames, *params.image_size, out)
    th = threading.Thread(target=render, name="ohs-demo-render", daemon=True)
    th.start()
    while th.is_alive():
        th.join(0.5)
        if cancelled is not None and cancelled():
            with lock:
                state["abandoned"] = True
                done = state["done"]
            if done:
                shutil.rmtree(tmp, ignore_errors=True)
            raise InterruptedError("demo generation cancelled")
        if progress is not None:
            n = _count_frames(tmp)
            progress(min(0.99, n / max(params.n_frames, 1)), f"Rendering simulator frame {min(n + 1, params.n_frames)} of {params.n_frames}")
    if state["error"] is not None:
        raise RuntimeError(f"demo generation failed: {state['error']}") from state["error"]
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    for attempt in range(20):  # Windows: a virus scanner may hold a file in the new folder briefly
        try:
            tmp.rename(out)
            break
        except OSError:
            if attempt == 19:
                raise
            time.sleep(0.1)
    log.info("simulator demo ready in %.1f s: %s", time.perf_counter() - t0, out)
    if progress is not None:
        progress(1.0, "Simulator demo ready")
    return out
