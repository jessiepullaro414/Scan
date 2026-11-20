"""Two simulated passes of the bracket through the server's routes, for ``tests/test_server_second_pass.py``
and the study behind ``VALIDATION.md`` ("A second pass that joins the first") (FEATURE_PLAN N2).

* :func:`first_recording`: the bracket scanned from one side only (a hand-held sweep of
  :data:`N_FIRST` frames from azimuth 20 degrees, as ``tests/guide_loop.py`` does it, after the scanner
  is held still for :data:`HOLD_S`: a replay loses its first frames before the scan starts, :class:`HeldStart`).
* :func:`scan_through_server`: a recording replayed (not looped) as the device, ``POST /api/scan/start``
  at once (with the given body), and the wait until the replay's end stops the scan, as Stop would.
* :func:`scan_world`: where a scan's own world lies in the simulator's (each scan's tracking starts
  fresh): from the camera path the app made for it and the replayed recording's true poses, by frame
  index (a replay keeps the recording's indices).
* :func:`pass_recording`: a pass of :data:`N_PASS` frames, a hand-held sweep round a pose the app
  suggested (its target and look direction, in the simulator's world).

Recordings go through the tests' cache (``tests/recordings.py``).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

N_FIRST = 90
N_PASS = 60
FPS = 30.0
SIZE = (640, 400)
HOLD_S = 2.0  # the first recording starts with the scanner held still this long, then eases into its sweep
EASE_S = 0.5


@dataclass(frozen=True)
class HeldStart:
    """``base`` held still at its start for ``hold`` seconds, then eased into (in ``ease`` seconds: the speed
    rises evenly, so the simulated IMU sees no jump) and run as it is. A replay loses its first frames before
    the scan starts (about 1.5 s for the first scan of a session): with the hold, those are still frames, which
    the processing leaves out by motion, and the whole sweep is recorded every time (found in review
    2026-11-20: 48 to 54 frames of the sweep were recorded, so the first scan and the area asked for changed
    from run to run)."""

    base: Any
    t_start: float
    hold: float = HOLD_S
    ease: float = EASE_S

    def warp(self, t: np.ndarray) -> np.ndarray:
        u = np.atleast_1d(np.asarray(t, dtype=np.float64)) - self.t_start - self.hold
        e = self.ease
        return self.t_start + np.where(u <= 0, 0.0, np.where(u < e, u * u / (2.0 * e), e / 2.0 + (u - e)))

    def poses(self, t: np.ndarray) -> np.ndarray:
        return self.base.poses(self.warp(t))

    def pose(self, t: float) -> np.ndarray:
        return self.poses(np.array([float(t)]))[0]


def first_recording(tmp: Path, size: tuple[int, int] = SIZE, seed: int = 0, azimuth: float = 20.0) -> Path:
    from ohs.sim.nominal import nominal_calibration
    from ohs.sim.scene import make_scene
    from ohs.sim.trajectory import HandheldTrajectory
    from recordings import scan_recording

    sc = make_scene("bracket", 0)
    cal = nominal_calibration(size, realistic=True)
    t0 = 100.0 + float(cal.imu.time_offset)
    base = HandheldTrajectory(target=sc.target, t0=t0, duration=N_FIRST / FPS, seed=seed, baseline=cal.stereo.baseline,
                              azimuth0_deg=azimuth, **dict(sc.meta.get("trajectory", {})))
    n = N_FIRST + int(round((HOLD_S + EASE_S / 2.0) * FPS))
    return scan_recording(Path(tmp) / f"first_{seed}_{size[0]}", scene="bracket", n_frames=n, image_size=size,
                          realistic=True, depth_every=0, seed=seed, trajectory=HeldStart(base, t0), overwrite=True)


def pass_recording(tmp: Path, target_w: Any, direction_w: Any, distance_mm: float, size: tuple[int, int] = SIZE,
                   seed: int = 7000) -> Path:
    """A pass round ``direction_w`` (unit, from the target to the scanner, simulator's world): the
    simulator's hand-held motion, 22 degrees a second, +-5 degrees of elevation, +-10 mm."""
    from ohs.sim.nominal import nominal_calibration
    from ohs.sim.trajectory import HandheldTrajectory
    from recordings import scan_recording

    cal = nominal_calibration(size, realistic=True)
    d = np.asarray(direction_w, dtype=np.float64)
    d = d / np.linalg.norm(d)
    el = math.degrees(math.asin(float(np.clip(d[2], -1.0, 1.0))))
    el = min(max(el, 8.0), 78.0)  # the motion model's range (it cannot look straight down)
    az = math.degrees(math.atan2(d[1], d[0]))
    # rounded, so the cache finds the same pass when the suggested pose moves by a hair
    target = [round(float(x), 0) for x in target_w]
    traj = HandheldTrajectory(target=np.asarray(target), t0=100.0 + float(cal.imu.time_offset), duration=N_PASS / FPS,
                              seed=seed, distance=(round(distance_mm) - 10.0, round(distance_mm) + 10.0),
                              elevation_deg=(round(el) - 5.0, round(el) + 5.0), azimuth0_deg=float(round(az)),
                              sweep_deg_s=22.0, baseline=cal.stereo.baseline, aim_jitter_mm=3.0)
    return scan_recording(Path(tmp) / f"pass_{seed}_{size[0]}", scene="bracket", n_frames=N_PASS, image_size=size,
                          realistic=True, depth_every=0, seed=seed, trajectory=traj, overwrite=True)


def wait(cond, timeout: float, what: str, period: float = 0.1) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = cond()
        if v:
            return v
        time.sleep(period)
    raise AssertionError(f"timed out after {timeout:.0f} s waiting for {what}")


def scan_through_server(url: str, recording: Path, body: dict, timeout: float = 120.0) -> dict:
    """Connect ``recording`` as a replay (no loop), ``POST /api/scan/start`` with ``body`` at once, and wait
    until the replay's end has stopped the scan. Returns ``{"start": response json, "scan_id"}``; the start's
    response is returned as is when it is not 200 (nothing is waited for then)."""
    import requests

    r = requests.post(url + "/api/device/connect", json={"source": "replay", "recording": str(recording), "loop": False},
                      timeout=60)
    assert r.status_code == 200, r.text
    r = requests.post(url + "/api/scan/start", json=body, timeout=60)
    if r.status_code != 200:
        requests.post(url + "/api/device/disconnect", timeout=30)
        return {"status": r.status_code, "start": r.json(), "scan_id": None}
    s = r.json()
    wait(lambda: requests.get(url + "/api/scan", timeout=30).json()["state"] == "idle", timeout, "the replay to end the scan")
    return {"status": 200, "start": s, "scan_id": s["scan_id"]}


def scan_world(source_recording: Path, trajectory_csv: Path) -> np.ndarray:
    """``T`` (4x4) taking points of the scan's world into the simulator's: the median over the frames
    with a pose of ``gt[i] @ inv(T_scan[i])`` (they agree to the tracking's drift)."""
    from ohs import geometry as G
    from ohs.io.recording import RecordingReader
    from ohs.track.posegraph import read_trajectory

    gt = RecordingReader(source_recording).ground_truth_poses()
    Ts = [gt[i] @ G.invert_se3(T) for i, _, ok, T in read_trajectory(trajectory_csv) if ok and i in gt]
    assert Ts, "no frame of the scan's camera path is in the replayed recording"
    k = len(Ts) // 2
    return np.asarray(Ts[k])
