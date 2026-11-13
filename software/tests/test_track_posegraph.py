"""Loop closure and pose-graph straightening (``ohs.track.posegraph``, Studio's "Straighten").

* **Pure parts** (no rendering): ``trajectory.csv`` round trip, keyframe spacing, loop candidates
  on a simulated orbit, the correction interpolation, and the simulator helpers that make loop
  data (:func:`ohs.sim.trajectory.add_drift`, :class:`ohs.sim.trajectory.SweepTrajectory`).
* **Synthetic drift** (the core, fast): one 400 deg orbit around the bracket (640x400, 60 frames
  rendered once for the module). Its true poses, or the true poses with a random-walk drift added,
  are straightened and compared with the truth before and after:

  - ``ground_truth.before/after``: :func:`ohs.app.process.trajectory_errors` (aligned at the
    first frame, which straightening keeps);
  - ``ground_truth.revisits``: at the places where the scan passes over an earlier part, the
    *gap* between the two passes' surfaces along their normals (what shows as doubled surfaces)
    and the *offset* of the observed point in any direction (sliding included).

* **No false loops**: true poses come back unchanged; a scan without a revisit comes back
  unchanged; deliberately wrong loop candidates fail verification; a wrong loop among right ones
  is pruned by the line process; and a correction that needs more bending than the drift model
  allows is refused (with the model not scaled to the loops).
* **Large drift** (research plan 4.4.1): 78 mm of steady drift, beyond ICP from the tracked pose,
  closes with a global first guess (FPFH + FGR) and a drift model scaled to the loops; a lone large
  loop is not used; the global guess finds a revisit 40 mm and 8 deg off.
* **Real drift** (``@pytest.mark.slow``): a back-and-forth sweep over the car panel processed by
  ``process_recording`` on the GPU and with ``OHS_COMPUTE=cpu``, then straightened.

Run with ``-s`` to see the ``METRICS`` lines.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import compute  # noqa: E402
from ohs.app.process import ProcessParams, _write_trajectory, process_recording  # noqa: E402
from ohs.geometry import invert_se3, make_se3, pose_error, so3_exp  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from recordings import scan_recording  # noqa: E402
from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration  # noqa: E402
from ohs.sim.scene import make_scene  # noqa: E402
from ohs.sim.trajectory import HandheldTrajectory, SweepTrajectory, add_drift, add_steady_drift, sample_frames  # noqa: E402
from ohs.track import posegraph as pg  # noqa: E402

ORBIT_FRAMES = 60
ORBIT_FPS = 5.0  # 12 s, 33 deg/s; the scan is sparse in time, which straightening does not care about
ORBIT_DEG = 400.0  # the last 40 deg pass over the start again
T_START = 100.0 + NOMINAL_TIME_OFFSET  # camera time of make_scan_recording's first frame


def metrics(name: str, **values: object) -> None:
    print("METRICS " + json.dumps({"test": name, **values}, default=float))


def summary(rep: dict) -> dict:
    """The numbers the METRICS lines and the report back need."""
    out = {k: rep[k] for k in ("reason", "keyframes", "loop_candidates", "loops_accepted", "correction_mm",
                               "correction_deg")}
    out["timings_s"] = {k: round(v, 3) for k, v in rep["timings_s"].items()}
    gt = rep.get("ground_truth")
    if gt:
        for k in ("before", "after"):
            g = gt[k]
            out[f"traj_{k}"] = {"rms_mm": g["rms_trans_mm"], "max_mm": g["trans_mm"]["max"],
                                "median_mm": g["trans_mm"]["median"], "final_mm": g["final_trans_mm"],
                                "max_deg": g["rot_deg"]["max"]}
            rv = gt.get("revisits", {}).get(k, {})
            if rv.get("n"):
                out[f"revisit_{k}"] = {"offset_rms_mm": rv["offset_mm"]["rms"], "offset_max_mm": rv["offset_mm"]["max"],
                                       "gap_rms_mm": rv.get("gap_mm", {}).get("rms"),
                                       "gap_max_mm": rv.get("gap_mm", {}).get("max")}
    return out


def rows_from(T: np.ndarray, idx: list[int], times: np.ndarray) -> list[tuple[int, float, bool, np.ndarray]]:
    return [(int(i), float(times[k]), True, T[k]) for k, i in enumerate(idx)]


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def orbit(tmp_path_factory) -> Path:
    """A 400 deg hand-held orbit around the bracket (640x400, realistic lenses, dots and lasers)."""
    calib = nominal_calibration((640, 400), realistic=True)
    sc = make_scene("bracket", 3)
    dur = ORBIT_FRAMES / ORBIT_FPS
    traj = HandheldTrajectory(target=sc.target, t0=T_START, duration=dur, seed=3, baseline=calib.stereo.baseline,
                              distance=(200.0, 280.0), elevation_deg=(35.0, 65.0), sweep_deg_s=ORBIT_DEG / dur)
    return scan_recording(tmp_path_factory.mktemp("orbit") / "rec", scene="bracket", n_frames=ORBIT_FRAMES,
                               fps=ORBIT_FPS, image_size=(640, 400), realistic=True, trajectory=traj, depth_every=0,
                               seed=3)


@pytest.fixture(scope="module")
def truth(orbit) -> tuple[list[int], np.ndarray, np.ndarray]:
    """``(frame indices, true T_wc in the first camera's frame (N,4,4), frame times)``."""
    reader = RecordingReader(orbit)
    gt = reader.ground_truth_poses()
    idx = sorted(gt)
    T = np.stack([invert_se3(gt[idx[0]]) @ gt[i] for i in idx])
    return idx, T, reader.times()


# --------------------------------------------------------------------------- pure parts
def test_trajectory_csv_round_trip_matches_process_recording(tmp_path):
    rng = np.random.default_rng(0)
    rows = [(i, 100.0 + i / 30, i != 3, make_se3(so3_exp(rng.normal(0, 0.3, 3)), rng.normal(0, 50, 3))) for i in range(6)]
    _write_trajectory(tmp_path / "process.csv", rows)  # the writer behind process_recording's trajectory.csv
    pg.write_trajectory(tmp_path / "ours.csv", rows)
    assert (tmp_path / "process.csv").read_text() == (tmp_path / "ours.csv").read_text()
    back = pg.read_trajectory(tmp_path / "process.csv")
    assert [r[:3] for r in back] == [(i, pytest.approx(t, abs=1e-6), ok) for i, t, ok, _ in rows]
    assert all(np.allclose(a[3], b[3], atol=1e-6) for a, b in zip(back, rows))


def test_keyframes_follow_travel_rotation_and_failures():
    # 300 mm straight line at 1 mm per frame, then a 60 deg turn in place at 1 deg per frame
    rows = [(i, i / 30, True, make_se3(t=[float(i), 0.0, 0.0])) for i in range(301)]
    rows += [(301 + k, (301 + k) / 30, True, make_se3(so3_exp([0.0, math.radians(k + 1), 0.0]), [300.0, 0, 0]))
             for k in range(60)]
    kf = pg.select_keyframes(rows, travel_mm=30.0, rotation_deg=10.0, max_gap_s=100.0)
    assert kf[0] == 0 and kf[-1] == len(rows) - 1
    assert kf[:11] == list(range(0, 301, 30))  # every 30 mm along the line
    assert len(kf) == 11 + 6  # then every 10 deg of the turn (the last frame is one of them)
    # a tracking failure: the frames on both sides become keyframes
    broken = [(i, t, not (140 <= i < 145), T) for i, t, _, T in rows]
    kf2 = pg.select_keyframes(broken, travel_mm=30.0, rotation_deg=10.0, max_gap_s=100.0)
    assert 139 in kf2 and 145 in kf2 and not any(140 <= k < 145 for k in kf2)
    # the cap widens the spacing
    assert len(pg.select_keyframes(rows, 30.0, 10.0, 100.0, max_keyframes=8)) <= 8


def test_loop_candidates_only_where_the_scan_comes_back():
    sc = make_scene("bracket", 3)
    traj = HandheldTrajectory(target=sc.target, t0=0.0, duration=12.0, seed=3, distance=(200.0, 280.0),
                              elevation_deg=(35.0, 65.0), sweep_deg_s=ORBIT_DEG / 12.0)
    times, T = sample_frames(traj, 240, 20.0)
    rows = [(i, float(times[i]), True, T[i]) for i in range(len(T))]
    p = pg.StraightenParams()
    kf = pg.select_keyframes(rows, p.kf_travel_mm, p.kf_rotation_deg, p.kf_max_gap_s)
    travel = pg._cumulative_travel(rows)[kf]
    cands = pg.find_loop_candidates(np.stack([rows[k][3] for k in kf]), travel, p)
    assert 3 <= len(cands) <= p.max_candidates
    for a, b in cands:
        assert b - a >= p.loop_min_keyframes and travel[b] - travel[a] >= p.loop_min_travel_mm
    # the strongest revisit is the end of the orbit coming back over its start
    assert any(kf[a] < 40 and kf[b] > 200 for a, b in cands)
    # no two candidates within candidate_spacing keyframes at both ends
    for x, (a, b) in enumerate(cands):
        for a2, b2 in cands[x + 1:]:
            assert abs(a - a2) > p.candidate_spacing or abs(b - b2) > p.candidate_spacing
    # the start of the same orbit, up to 140 mm of camera travel, never comes back: no candidates. (Its
    # first 100 deg already do: from high up, views 60-80 deg apart look at the same spot from within
    # 35 deg, and ICP between them is a genuine long-baseline constraint.)
    n = int(np.searchsorted(pg._cumulative_travel(rows), 140.0, side="right"))
    short = rows[:n]
    kf_s = pg.select_keyframes(short, p.kf_travel_mm, p.kf_rotation_deg, p.kf_max_gap_s)
    assert len(kf_s) >= 4
    assert pg.find_loop_candidates(np.stack([short[k][3] for k in kf_s]), pg._cumulative_travel(short)[kf_s], p) == []


def test_interpolation_keeps_frames_relative_to_keyframes():
    rng = np.random.default_rng(1)
    times = np.linspace(0.0, 2.0, 21)
    T_in = np.stack([make_se3(so3_exp(rng.normal(0, 0.2, 3)), rng.normal(0, 100, 3)) for _ in times])
    kf = [0, 10, 20]
    C = make_se3(so3_exp([0.01, -0.02, 0.015]), [1.0, -2.0, 0.5])  # one rigid change of the world
    out = pg.interpolate_poses(times, T_in, times[kf], T_in[kf], np.stack([C @ T_in[k] for k in kf]))
    assert np.allclose(out, C[None] @ T_in, atol=1e-9)
    # different corrections: keyframes land exactly, frames in between blend smoothly
    C2 = [np.eye(4), make_se3(t=[2.0, 0.0, 0.0]), make_se3(so3_exp([0.0, 0.0, 0.02]), [2.0, 1.0, 0.0])]
    out = pg.interpolate_poses(times, T_in, times[kf], T_in[kf], np.stack([C2[m] @ T_in[k] for m, k in enumerate(kf)]))
    for m, k in enumerate(kf):
        assert np.allclose(out[k], C2[m] @ T_in[k], atol=1e-9)
    shift = [np.linalg.norm(out[f, :3, 3] - T_in[f, :3, 3]) for f in range(11)]
    assert np.allclose(shift, np.linspace(0.0, 2.0, 11), atol=1e-9)  # half way in time, half the correction


def test_add_drift_is_a_growing_random_walk():
    sc = make_scene("bracket", 0)
    _, T = sample_frames(HandheldTrajectory(target=sc.target, duration=4.0, seed=0), 400, 100.0)
    assert np.allclose(add_drift(T, 0.0, 0.0, 0), T, atol=1e-9)
    D = add_drift(T, 0.05, 0.2, 0)
    assert np.allclose(D[0], T[0])
    # per-frame errors of the relative motions have the requested RMS size
    step = [pose_error(invert_se3(T[k - 1]) @ T[k], invert_se3(D[k - 1]) @ D[k]) for k in range(1, len(T))]
    assert np.sqrt(np.mean([s[0] ** 2 for s in step])) == pytest.approx(0.05, rel=0.15)
    assert np.sqrt(np.mean([s[1] ** 2 for s in step])) == pytest.approx(0.2, rel=0.15)
    err = [pose_error(T[k], D[k])[1] for k in range(len(T))]
    assert np.mean(err[300:]) > 2.0 * np.mean(err[1:100])  # it accumulates
    assert not np.allclose(add_drift(T, 0.05, 0.2, 1), D)  # seeded


def test_sweep_trajectory_revisits_the_strip_beside_the_first_pass():
    sc = make_scene("car_panel", 0)
    traj = SweepTrajectory(target=sc.target, t0=0.0, duration=8.0, passes=2, length_mm=200.0, spacing_mm=50.0,
                           heading_deg=15.0, seed=0)
    t = np.linspace(0.0, 8.0, 801)
    T = traj.poses(t)
    R = T[:, :3, :3]
    assert np.allclose(np.einsum("nij,nik->njk", R, R), np.eye(3), atol=1e-12)
    assert np.allclose(traj.pose(3.21), traj.poses(np.array([3.21]))[0])
    centre = T[:, :3, 3] + 30.0 * R[:, :, 0]
    look = centre + 265.0 * R[:, :, 2]  # roughly where the scanner looks
    h = np.radians(15.0)
    along = (look - sc.target) @ np.array([math.cos(h), math.sin(h), 0.0])
    side = (look - sc.target) @ np.array([-math.sin(h), math.cos(h), 0.0])
    assert along[0] < -80 and along[400] > 80 and along[-1] < -80  # out along the strip and back
    assert abs(np.median(side[:250]) - np.median(side[-250:]) + 50.0) < 15.0  # the second pass is 50 mm beside
    v = np.linalg.norm(np.diff(centre, axis=0), axis=1) / np.diff(t)
    assert v.max() < 200.0  # mm/s: hand speed, smooth turnarounds
    with pytest.raises(ValueError):
        SweepTrajectory(target=sc.target, elevation_deg=(60.0, 88.0))


# --------------------------------------------------------------------------- synthetic drift on the orbit
def test_true_poses_come_back_unchanged(orbit, truth):
    idx, T, times = truth
    rows = rows_from(T, idx, times)
    poses, rep = pg.straighten_trajectory(orbit, None, rows)
    metrics("straighten_orbit_truth", **summary(rep))
    assert rep["loop_candidates"] >= 3 and rep["loops_accepted"] >= 3
    assert rep["reason"] == "already_straight" and rep["changed"] is False
    assert rep["residual_mm"]["before_max"] < 0.12  # what loops measure on perfect poses: their noise
    assert rep["correction_mm"] == 0.0 and rep["verdict"].startswith("Closed")
    assert set(poses) == set(idx) and all(np.array_equal(poses[i], T[k]) for k, i in enumerate(idx))
    assert json.loads(json.dumps(rep, allow_nan=False))["reason"] == "already_straight"  # the server stores it as JSON


@pytest.mark.parametrize("rot_deg,trans_mm,seed", [(0.05, 0.2, 1), (0.1, 0.3, 2)])
def test_synthetic_drift_is_straightened(orbit, truth, rot_deg, trans_mm, seed):
    idx, T, times = truth
    drifted = add_drift(T, rot_deg, trans_mm, seed)
    rows = rows_from(drifted, idx, times)
    calls: list[tuple[int, int]] = []
    poses, rep = pg.straighten_trajectory(orbit, None, rows, progress=lambda k, n: calls.append((k, n)))
    m = summary(rep)
    metrics(f"straighten_orbit_drift_{rot_deg}_{trans_mm}", **m)
    assert rep["reason"] == "straightened" and rep["changed"] is True, rep["verdict"]
    assert rep["loops_accepted"] >= 3 and len(rep["loops"]) == rep["loops_accepted"]
    assert all(i in poses and j in poses and j > i for i, j in rep["loops"])
    assert rep["verdict"].startswith(f"Closed {rep['loops_accepted']} loops: ") and "mm into place" in rep["verdict"]
    assert 0.5 < rep["correction_mm"] < 20.0 and rep["correction_deg"] > 0.05
    # the passes line up: the gap between their surfaces drops tenfold, the offset of what they see too
    gap_b, gap_a = m["revisit_before"]["gap_rms_mm"], m["revisit_after"]["gap_rms_mm"]
    assert gap_b > 0.5 and gap_a < 0.2 and gap_a < 0.15 * gap_b
    # (the offset includes sliding along the table, which no loop can see, so it drops less)
    assert m["revisit_after"]["offset_rms_mm"] < 0.75 * m["revisit_before"]["offset_rms_mm"]
    assert rep["residual_mm"]["after_max"] < 0.35 < rep["residual_mm"]["before_max"]
    # and the whole trajectory is closer to the truth
    assert m["traj_after"]["rms_mm"] < m["traj_before"]["rms_mm"]
    assert m["traj_after"]["median_mm"] < m["traj_before"]["median_mm"] + 0.1
    # every tracked frame, the first (the world frame) unchanged
    assert set(poses) == set(idx) and np.array_equal(poses[idx[0]], drifted[0])
    # progress: k of n steps done, increasing, ending at (n, n)
    assert calls and calls[0][0] == 1 and calls[-1][0] == calls[-1][1]
    assert [k for k, _ in calls] == sorted(k for k, _ in calls)
    json.dumps(rep, allow_nan=False)  # plain JSON, as the server stores it


def test_straighten_reads_trajectory_csv_and_uses_given_calibration(orbit, truth, tmp_path):
    idx, T, times = truth
    rows = rows_from(add_drift(T, 0.05, 0.2, 1), idx, times)
    pg.write_trajectory(tmp_path / "trajectory.csv", rows)
    calib = RecordingReader(orbit).calibration()
    poses_a, rep_a = pg.straighten_trajectory(orbit, calib, tmp_path / "trajectory.csv")
    poses_b, rep_b = pg.straighten_trajectory(orbit, None, rows)
    assert rep_a["loops"] == rep_b["loops"]
    assert max(np.abs(poses_a[i] - poses_b[i]).max() for i in idx) < 1e-3  # csv rounding only
    wrong = nominal_calibration((1280, 800))
    with pytest.raises(ValueError, match="640x400"):
        pg.straighten_trajectory(orbit, wrong, rows)


def test_scan_without_revisit_is_unchanged(orbit, truth):
    """The start of the orbit, up to 140 mm of camera travel (a short scan, like the validated 36-frame
    ones): it never comes back over the same place, with or without drift."""
    idx, T, times = truth
    travel = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(T[:, :3, 3], axis=0), axis=1))])
    n = int(np.searchsorted(travel, 140.0, side="right"))
    assert n >= 5
    for Tn, name in ((T, "truth"), (add_drift(T, 0.1, 0.3, 4), "drift")):
        rows = rows_from(Tn[:n], idx[:n], times)
        calls: list[tuple[int, int]] = []
        poses, rep = pg.straighten_trajectory(orbit, None, rows, progress=lambda k, m: calls.append((k, m)))
        metrics(f"straighten_short_scan_{name}", frames=n, travel_mm=float(travel[n - 1]), **summary(rep))
        assert calls == [(1, 1)]  # an early answer still reports completion
        assert rep["loop_candidates"] == 0 and rep["loops_accepted"] == 0 and rep["loops"] == []
        assert rep["reason"] == "no_candidates" and rep["changed"] is False
        assert rep["verdict"].startswith("No loops found")
        assert all(np.array_equal(poses[i], rows[k][3]) for k, i in enumerate(idx[:n]))
    # fewer than two tracked frames
    _, rep1 = pg.straighten_trajectory(orbit, None, [(r[0], r[1], k == 0, r[3]) for k, r in enumerate(rows)])
    assert rep1["reason"] == "too_short" and rep1["keyframes"] == 0


def _orbit_loops(orbit, rows, params=None):
    """Keyframes, odometry edges and verified loops of ``rows`` (the steps of straighten_trajectory)."""
    p = params or pg.StraightenParams()
    reader = RecordingReader(orbit)
    kf = pg.select_keyframes(rows, p.kf_travel_mm, p.kf_rotation_deg, p.kf_max_gap_s)
    T_kf = np.stack([rows[k][3] for k in kf])
    travel = pg._cumulative_travel(rows)[kf]
    clouds = pg.KeyframeClouds(reader, reader.calibration(), p)
    pos = {int(i): k for k, i in enumerate(reader.indices)}
    loops = []
    for a, b in pg.find_loop_candidates(T_kf, travel, p):
        chk = pg.verify_loop(clouds.get(pos[rows[kf[a]][0]]), clouds.get(pos[rows[kf[b]][0]]),
                             invert_se3(T_kf[b]) @ T_kf[a], p, p.drift_slack_mm + p.drift_frac * (travel[b] - travel[a]), a, b)
        if chk.accepted:
            loops.append(chk)
    odo = pg.odometry_edges(T_kf, np.diff(kf), None, p)
    return kf, T_kf, odo, loops, clouds, pos


def test_wrong_loop_candidates_fail_verification(orbit, truth):
    idx, T, times = truth
    rows = rows_from(T, idx, times)
    kf, T_kf, _, loops, clouds, pos = _orbit_loops(orbit, rows)
    L = max(loops, key=lambda c: c.b - c.a)  # the orbit's end over its start
    src, dst = clouds.get(pos[rows[kf[L.a]][0]]), clouds.get(pos[rows[kf[L.b]][0]])
    T_true = invert_se3(T_kf[L.b]) @ T_kf[L.a]
    ok = pg.verify_loop(src, dst, T_true, None, 20.0)
    assert ok.accepted and ok.moved_mm < 0.3, ok.reason
    # the trajectory claims the two views are 25 mm and 6 deg (a tilt of the table) away from where they are
    bad = pg.verify_loop(src, dst, T_true @ make_se3(so3_exp([0.0, math.radians(6.0), 0.0]), [25.0, 0.0, 0.0]), None, 20.0)
    assert not bad.accepted and bad.reason, bad.summary()
    # ... or 150 mm farther along the view: nothing is close enough to match
    far = pg.verify_loop(src, dst, T_true @ make_se3(t=[0.0, 0.0, 150.0]), None, 20.0)
    assert not far.accepted and ("matching points" in far.reason or "overlap" in far.reason), far.summary()
    # ... or 12 mm off along the direction the surfaces fix best, more than the drift allowed between them
    D, _, U = pg._scaled_subspace(ok.J2, 250.0, 0.0)
    off = pg.verify_loop(src, dst, T_true @ pg._exp6(12.0 * D * U[:, -1]), None, 6.0)
    assert not off.accepted, off.summary()
    # the views from the opposite side of the orbit, claimed to be 40 mm closer than they are
    a, b = 0, len(kf) // 2
    side = pg.verify_loop(clouds.get(pos[rows[kf[a]][0]]), clouds.get(pos[rows[kf[b]][0]]),
                          invert_se3(T_kf[b]) @ T_kf[a] @ make_se3(t=[0.0, 0.0, -40.0]), None, 20.0)
    assert not side.accepted, side.summary()


def test_line_process_prunes_a_loop_that_disagrees(orbit, truth):
    """The end of the orbit comes back over its start in several loops between different keyframes.
    If one of them were wrong (2.5 mm off along the direction it measures best), the others outvote it:
    the line process prunes it and the result is the one without it."""
    idx, T, times = truth
    rows = rows_from(add_drift(T, 0.05, 0.2, 1), idx, times)
    kf, T_kf, odo, loops, _, _ = _orbit_loops(orbit, rows)
    _, kept_all = pg.optimize_pose_graph(T_kf, odo, loops)
    assert len(loops) >= 4 and len(kept_all) == len(loops)
    w = max(range(len(loops)), key=lambda n: loops[n].b - loops[n].a)  # the longest: end over start
    span = [n for n, c in enumerate(loops) if c.b - c.a > 0.6 * len(kf)]
    assert len(span) >= 2, "the orbit's revisit needs more than one loop for this test"
    L = loops[w]
    D, _, U = pg._scaled_subspace(L.J2, 250.0, 0.0)  # rotations as mm at 250 mm
    wrong = pg.LoopCheck(**{**L.__dict__, "T_ba": L.T_ba @ pg._exp6(2.5 * D * U[:, -1])})
    others = loops[:w] + loops[w + 1:]
    T_ref, _ = pg.optimize_pose_graph(T_kf, odo, others)
    assert pg.loop_residual_mm(T_ref, wrong) > 1.5
    T_opt, kept = pg.optimize_pose_graph(T_kf, odo, loops[:w] + [wrong] + loops[w + 1:])
    assert w not in kept and len(kept) == len(loops) - 1
    assert np.abs(T_opt[:, :3, 3] - T_ref[:, :3, 3]).max() < 0.05  # as if it had not been there


def test_a_correction_the_drift_model_cannot_explain_is_refused(orbit, truth):
    """With a drift model 100x tighter than the real drift (and not scaled to what the loops show),
    closing the loops would bend the odometry edges far beyond it: the scan must come back
    unchanged, with a verdict that says why."""
    idx, T, times = truth
    rows = rows_from(add_drift(T, 0.05, 0.2, 1), idx, times)
    p = pg.StraightenParams(drift_mm_per_frame=0.002, drift_deg_per_frame=0.0005, adaptive_drift=False)
    poses, rep = pg.straighten_trajectory(orbit, None, rows, params=p)
    metrics("straighten_refused", reason=rep["reason"], verdict=rep["verdict"], residual=rep["residual_mm"])
    assert rep["reason"] in ("bends_too_much", "disagree", "not_better"), rep["verdict"]
    assert rep["changed"] is False and rep["loops_accepted"] == 0 and rep["loops"] == []
    assert "unchanged" in rep["verdict"]
    assert all(np.array_equal(poses[i], rows[k][3]) for k, i in enumerate(idx))


# --------------------------------------------------------------------------- large drift (4.4.1)
def test_large_steady_drift_is_straightened(orbit, truth):
    """78 mm of steady drift by the end of the orbit (0.8 deg and 4 mm after every frame): the end comes
    back over the start about 50 mm off, beyond ICP from the tracked pose (20 mm) and the drift model.
    Without the large-drift steps nothing verifies; with them the doubled surfaces close."""
    idx, T, times = truth
    rows = rows_from(add_steady_drift(T, 0.8, 4.0, 0), idx, times)
    old = pg.StraightenParams(global_registration=False, adaptive_drift=False)
    poses0, rep0 = pg.straighten_trajectory(orbit, None, rows, params=old)
    assert rep0["changed"] is False and rep0["reason"] in ("not_verified", "disagree"), rep0["verdict"]
    poses, rep = pg.straighten_trajectory(orbit, None, rows)
    m = summary(rep)
    metrics("straighten_orbit_large_drift", **m, drift_scale=rep["drift_scale"],
            via_global=sum(c["via"] == "global" and c["accepted"] for c in rep["candidates"]))
    assert rep["reason"] == "straightened" and rep["changed"] is True, rep["verdict"]
    assert rep["drift_scale"] > 5 and rep["loops_accepted"] >= 4
    assert any(c["large"] and c["accepted"] for c in rep["candidates"])
    gap_b, gap_a = m["revisit_before"]["gap_rms_mm"], m["revisit_after"]["gap_rms_mm"]
    assert gap_b > 20 and gap_a < 0.15 * gap_b
    assert m["traj_after"]["rms_mm"] < m["traj_before"]["rms_mm"]
    assert np.array_equal(poses[idx[0]], rows[0][3])


def test_a_lone_large_loop_is_not_used(orbit, truth):
    """With one candidate only, a large correction has nothing to agree with: the scan stays as it is."""
    idx, T, times = truth
    rows = rows_from(add_steady_drift(T, 0.8, 4.0, 0), idx, times)
    poses, rep = pg.straighten_trajectory(orbit, None, rows, params=pg.StraightenParams(max_candidates=1))
    assert rep["loop_candidates"] == 1 and rep["changed"] is False and rep["reason"] == "not_verified"
    assert "second loop" in rep["candidates"][0]["reason"] or not rep["candidates"][0]["large"]


def test_the_global_guess_finds_a_far_off_revisit(orbit, truth):
    """The end over the start, claimed 40 mm and 8 deg away from where it is: ICP from that claim fails;
    FPFH + FGR find the match, and ICP from there lines the views up along what they measure. The
    directions they don't measure keep the claim."""
    idx, T, times = truth
    rows = rows_from(T, idx, times)
    kf, T_kf, _, loops, clouds, pos = _orbit_loops(orbit, rows)
    L = max(loops, key=lambda c: c.b - c.a)
    src, dst = clouds.get(pos[rows[kf[L.a]][0]]), clouds.get(pos[rows[kf[L.b]][0]])
    T_true = invert_se3(T_kf[L.b]) @ T_kf[L.a]
    claim = T_true @ make_se3(so3_exp([math.radians(8.0), 0.0, 0.0]), [40.0, 0.0, 0.0])
    assert not pg.verify_loop(src, dst, claim, None, 60.0).accepted
    chk, tried = None, []
    for method in pg.GLOBAL_METHODS:  # as Straighten does: FGR, then RANSAC, each only as a start
        start = pg.global_match(src, dst, None, method)
        tried.append((method, start is not None and pg._plausible(src, claim, start, 60.0, 20.0)))
        if start is None or not tried[-1][1]:
            continue
        chk = pg.verify_loop(src, dst, claim, None, 60.0, start=start, large=True)
        if chk.accepted:
            break
    assert chk is not None and chk.accepted and chk.large, (tried, chk.summary() if chk else None)
    e = pg._vec6(invert_se3(T_true) @ chk.T_ba)
    assert math.sqrt(max(float(e @ L.J2 @ e), 0.0)) < 0.3  # on the truth along the measured directions


def test_cancel_stops_between_steps(orbit, truth):
    idx, T, times = truth
    rows = rows_from(add_drift(T, 0.05, 0.2, 1), idx, times)
    seen: list[int] = []
    with pytest.raises(InterruptedError):
        pg.straighten_trajectory(orbit, None, rows, progress=lambda k, n: seen.append(k), cancelled=lambda: len(seen) >= 3)
    assert len(seen) == 3


# --------------------------------------------------------------------------- real drift (the pipeline)
@pytest.fixture
def cpu_only(monkeypatch):
    monkeypatch.setenv(compute.ENV_VAR, "cpu")
    compute.reset()
    yield
    monkeypatch.delenv(compute.ENV_VAR, raising=False)
    compute.reset()


def _sweep_recording(path: Path) -> Path:
    """Two back-and-forth passes over the car panel, 50 mm apart (640x400, 180 frames at 15 fps)."""
    calib = nominal_calibration((640, 400), realistic=True)
    sc = make_scene("car_panel", 0)
    traj = SweepTrajectory(target=sc.target, t0=T_START, duration=12.0, passes=2, length_mm=200.0, spacing_mm=50.0,
                           heading_deg=15.0, elevation_deg=(60.0, 78.0), seed=0, baseline=calib.stereo.baseline)
    return scan_recording(path, scene="car_panel", n_frames=180, fps=15.0, image_size=(640, 400), realistic=True,
                               trajectory=traj, depth_every=0, seed=0)


def _real_drift_run(rec: Path, out: Path, name: str) -> None:
    rep = process_recording(rec, out, params=ProcessParams())
    assert rep["frames"]["tracked_fraction"] > 0.95
    rows = pg.read_trajectory(out / "trajectory.csv")
    poses, srep = pg.straighten_trajectory(rec, None, rows)
    m = summary(srep)
    metrics(f"straighten_{name}", compute=rep["params"]["compute"], **m)
    # never worse: unchanged, or closer to the truth
    assert srep["reason"] in ("straightened", "already_straight"), srep["verdict"]
    assert m["traj_after"]["rms_mm"] <= m["traj_before"]["rms_mm"] + 0.02
    assert m["traj_after"]["max_mm"] <= m["traj_before"]["max_mm"] + 0.1
    if "revisit_after" in m and m["revisit_after"]["gap_rms_mm"] is not None:
        assert m["revisit_after"]["gap_rms_mm"] <= m["revisit_before"]["gap_rms_mm"] + 0.02
    # drift added on top of the tracked poses is taken out again where the panel can measure it
    idx = [i for i, _, ok, _ in rows if ok]
    T = np.stack([T for _, _, ok, T in rows if ok])
    t = [t for _, t, ok, _ in rows if ok]
    drifted = add_drift(T, 0.05, 0.2, 11)
    poses2, srep2 = pg.straighten_trajectory(rec, None, [(i, t[k], True, drifted[k]) for k, i in enumerate(idx)])
    m2 = summary(srep2)
    metrics(f"straighten_{name}_plus_drift", **m2)
    assert srep2["reason"] == "straightened", srep2["verdict"]
    assert m2["revisit_after"]["gap_rms_mm"] < 0.35 * m2["revisit_before"]["gap_rms_mm"]
    assert m2["traj_after"]["rms_mm"] <= m2["traj_before"]["rms_mm"] + 0.1


@pytest.fixture(scope="module")
def sweep(tmp_path_factory) -> Path:
    return _sweep_recording(tmp_path_factory.mktemp("sweep") / "rec")


@pytest.mark.slow
def test_real_drift_panel_sweep_gpu(sweep, tmp_path):
    if compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    _real_drift_run(sweep, tmp_path / "out", "panel_sweep_gpu")


@pytest.mark.slow
def test_real_drift_panel_sweep_cpu(sweep, tmp_path, cpu_only):
    _real_drift_run(sweep, tmp_path / "out", "panel_sweep_cpu")
