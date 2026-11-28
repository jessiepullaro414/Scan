"""The laser crossings (``ohs.track.curve_ba.laser_curve_adjustment``) on a recording whose frames come in turn: dot
frames and laser frames (``ohs.app.process``, "Frames in turn").

The trajectory passed in holds the dot frames (``trajectory.csv`` of an in-turn recording). A dot frame has no laser
line, so the laser curves come from the ``lasers`` frames (and ``both`` frames, as today), each laser frame placed
between the given dot frames beside it in time by the frame pass's rule (``ohs.track.pose_between``). The result is a
pose for every tracked frame of the trajectory passed in, as before.

**A recording without kinds gives exactly the same poses and report as before** (the guard below): its digests were
taken on the code before the crossings learnt frames in turn (2026-11-28, on the owner's mini PC, processor path:
``OHS_COMPUTE=cpu``). They depend on the exact floating point of numpy and SciPy; if they differ on another machine,
check first that the unchanged code gives other digests there. Timings are left out of the report's digest.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil

import numpy as np
import pytest

from ohs.geometry import invert_se3
from ohs.io.recording import RecordingReader
from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration
from ohs.sim.scene import make_scene
from ohs.sim.trajectory import HandheldTrajectory, add_drift
from ohs.track.curve_ba import CurveParams, laser_curve_adjustment
from recordings import scan_recording

SIZE = (640, 400)
CPU_ONLY = pytest.mark.skipif(os.environ.get("OHS_COMPUTE", "").lower() != "cpu",
                              reason="the reference digests are the processor path's (OHS_COMPUTE=cpu)")


# --------------------------------------------------------------------------- recordings
def _orbit_trajectory(duration: float = 12.0):
    calib = nominal_calibration(SIZE, realistic=True)
    sc = make_scene("sphere_on_plane", 7)
    return HandheldTrajectory(target=sc.target, t0=100.0 + NOMINAL_TIME_OFFSET, duration=duration, seed=7,
                              baseline=calib.stereo.baseline, distance=(220.0, 300.0), elevation_deg=(35.0, 65.0),
                              sweep_deg_s=400.0 / duration)


def _orbit(path, n_frames: int = 60, fps: float = 5.0, light=None):
    """The 400 deg orbit around the sphere of ``tests/test_track_curve_ba.py`` (the same recording, from the cache,
    without ``light``)."""
    kw = {} if light is None else {"light": light}
    return scan_recording(path, scene="sphere_on_plane", n_frames=n_frames, fps=fps, image_size=SIZE, realistic=True,
                          trajectory=_orbit_trajectory(), depth_every=0, seed=7, **kw)


def _true_rows(rec, keep=None):
    """``[(index, t, True, T)]`` of the frames ``keep`` (default all) at their true poses, the world on the first."""
    reader = RecordingReader(rec)
    gt = reader.ground_truth_poses()
    times = reader.times()
    pos = {int(i): k for k, i in enumerate(reader.indices)}
    idx = sorted(i for i in gt if keep is None or keep(i))
    A = invert_se3(gt[idx[0]])
    return [(i, float(times[pos[i]]), True, A @ gt[i]) for i in idx]


def _drifted(rows, seed: int = 3):
    D = add_drift(np.stack([r[3] for r in rows]), 0.2, 0.6, seed)
    return [(i, t, ok, D[k]) for k, (i, t, ok, _) in enumerate(rows)]


def _reference(rec):
    """The sphere on its table as a triangle mesh (UV sphere r = 40 mm on a 600 mm square), in the world of
    :func:`_true_rows` (the first frame's camera)."""
    reader = RecordingReader(rec)
    gt = reader.ground_truth_poses()
    A = invert_se3(gt[min(gt)])
    nu, nv = 48, 24
    th, ph = np.meshgrid(np.linspace(0, 2 * np.pi, nu, endpoint=False), np.linspace(0, np.pi, nv + 1))
    S = np.stack([40 * np.sin(ph) * np.cos(th), 40 * np.sin(ph) * np.sin(th), 40 + 40 * np.cos(ph)], -1).reshape(-1, 3)
    F = []
    for r in range(nv):
        for c in range(nu):
            a, b, d, e = r * nu + c, r * nu + (c + 1) % nu, (r + 1) * nu + c, (r + 1) * nu + (c + 1) % nu
            F += [(a, d, b), (b, d, e)]
    g = np.linspace(-300, 300, 61)
    X, Y = np.meshgrid(g, g)
    P = np.stack([X, Y, np.zeros_like(X)], -1).reshape(-1, 3)
    o = len(S)
    for r in range(60):
        for c in range(60):
            a, b, d, e = o + r * 61 + c, o + r * 61 + c + 1, o + (r + 1) * 61 + c, o + (r + 1) * 61 + c + 1
            F += [(a, b, d), (b, e, d)]
    V = np.concatenate([S, P]) @ A[:3, :3].T + A[:3, 3]
    return V, np.asarray(F, dtype=np.int64)


# --------------------------------------------------------------------------- the guard: no kinds, as before
_VOLATILE = {"timings_s"}


def digest(poses: dict, rep: dict) -> dict:
    """What must not change: the poses bit for bit, the report's numbers and words without its timings."""
    h = hashlib.sha256()
    for i in sorted(poses):
        h.update(np.int64(i).tobytes())
        h.update(np.ascontiguousarray(np.asarray(poses[i], dtype=np.float64)).tobytes())
    r = {k: v for k, v in rep.items() if k not in _VOLATILE}
    return {"frames": len(poses), "poses": h.hexdigest(), "reason": rep["reason"],
            "report": hashlib.sha256(json.dumps(r, sort_keys=True, default=float).encode()).hexdigest()}


@pytest.fixture(scope="module")
def orbit(tmp_path_factory):
    return _orbit(tmp_path_factory.mktemp("cbaturn") / "orbit")


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    """A second recording without kinds: 24 frames of the default scene."""
    return scan_recording(tmp_path_factory.mktemp("cbaturn") / "small", n_frames=24, image_size=SIZE, realistic=True,
                          seed=4, depth_every=0)


def _guard_case(case: str, orbit, small):
    if case == "orbit_drift":
        return laser_curve_adjustment(orbit, None, _drifted(_true_rows(orbit)))
    if case == "orbit_drift_reference_30_frames":
        return laser_curve_adjustment(orbit, None, _drifted(_true_rows(orbit)), reference=_reference(orbit),
                                      params=CurveParams(max_frames=30))
    # (24 frames cross about 110 to 130 times: fewer than the default's 150, so the cases below ask for 50)
    if case == "small_true":
        return laser_curve_adjustment(small, None, _true_rows(small), params=CurveParams(min_crossings=50))
    if case == "small_drift_crossings":
        return laser_curve_adjustment(small, None, _drifted(_true_rows(small)),
                                      params=CurveParams(holdout="crossings", min_crossings=50))
    if case == "small_drift_few":
        return laser_curve_adjustment(small, None, _drifted(_true_rows(small)))
    raise KeyError(case)


#: what the unchanged code gave (module docstring), processor path
REFERENCE: dict[str, dict | None] = {
    "orbit_drift": {"frames": 60, "poses": "3f0cb1671d517136ef5e002745e42831261b39bcdb2a574aaa2ffdd39f7c9b99",
                    "reason": "adjusted", "report": "d100468325d651c26ac724421ddad894c633f9565022f34cd7a94295d82496d1"},
    "orbit_drift_reference_30_frames": {
        "frames": 60, "poses": "fb5565bdc22c02473aacafff8e1f25b5e073b4c26d04ccf0e220bb4843ed41d5", "reason": "adjusted",
        "report": "f52956ae399979df5a7ca105d466557247e176af7b217f89612d2441b670711b"},
    "small_true": {"frames": 24, "poses": "09a405a17e8c89652055882fab76f788ad443c78ed67076f2132ab8f6f7f5090",
                   "reason": "adjusted", "report": "37d414c0c2e52de65ee9bf92e0fa296c12f70b30dbe03d2c70f63de70f06f003"},
    "small_drift_crossings": {
        "frames": 24, "poses": "a4ea6f80a76b859e813bd4ff9cacdd5c8f1c2b36511dd83f12ebdf01d0d326a1", "reason": "adjusted",
        "report": "8571a2f49c7218be06b6e14a7d811883163f8d180e12188de4a95ae408bfb19b"},
    "small_drift_few": {"frames": 24, "poses": "cd6bf3c239807f183d22cb510d6745a2ff047c6784832953551a5f8c29cdb3b4",
                        "reason": "few_crossings",
                        "report": "7d740931f7e3b762682807e551c63fa96824202c99c06bb382665b3b12a57d89"},
}


@CPU_ONLY
@pytest.mark.parametrize("case", sorted(REFERENCE))
def test_a_recording_without_kinds_gives_the_same_poses_and_report(case, orbit, small):
    got = digest(*_guard_case(case, orbit, small))
    print(case, got)
    assert REFERENCE[case] is not None, f"no reference yet; this run gave {got}"
    assert got == REFERENCE[case]


@CPU_ONLY
def test_a_kinds_column_of_both_only_changes_nothing(orbit, tmp_path):
    """A recording written with kinds whose every frame holds both lights is a recording of today."""
    both = tmp_path / "both"
    shutil.copytree(orbit, both)
    lines = (both / "frames.csv").read_text(encoding="utf-8").splitlines()
    (both / "frames.csv").write_text("\n".join([lines[0] + ",kind"] + [ln + ",both" for ln in lines[1:]]) + "\n",
                                     encoding="utf-8")
    r = RecordingReader(both)
    assert r.has_kinds and set(r.kinds()) == {"both"}
    got = digest(*laser_curve_adjustment(both, None, _drifted(_true_rows(both))))
    assert REFERENCE["orbit_drift"] is not None
    assert got == REFERENCE["orbit_drift"]


# --------------------------------------------------------------------------- frames in turn
@pytest.fixture(scope="module")
def orbit_in_turn(tmp_path_factory):
    """The same orbit in turn: 120 frames at 10 a second, a dot frame then a laser frame (60 of each)."""
    return _orbit(tmp_path_factory.mktemp("cbaturn") / "orbit_turn", n_frames=120, fps=10.0, light="in_turn")


def _kind_of(rec) -> dict[int, str]:
    reader = RecordingReader(rec)
    return {int(i): str(k) for i, k in zip(reader.indices, reader.kinds())}


def _dot_rows(rec):
    kind = _kind_of(rec)
    return _true_rows(rec, keep=lambda i: kind[i] in ("dots", "both"))


@pytest.fixture
def read_frames(monkeypatch):
    """The frames whose laser lines the adjustment reads."""
    import ohs.track.curve_ba as cba

    seen: list[int] = []
    real = cba.frame_curves

    def counted(reader, calib, frames, *a, **kw):
        seen.extend(int(f) for f in frames)
        return real(reader, calib, frames, *a, **kw)

    monkeypatch.setattr(cba, "frame_curves", counted)
    return seen


def _with_events(src, dst, rows):
    import csv

    shutil.copytree(src, dst)
    with open(dst / "events.csv", "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for t, kind, value in rows:
            w.writerow([f"{t:.6f}", kind, value])
    return dst


def test_in_turn_the_curves_come_from_the_laser_frames(orbit_in_turn, read_frames):
    """At the true dot poses: the laser lines are read in the laser frames only, each placed between the dot frames
    beside it; the poses come back for exactly the dot frames passed in."""
    rec = orbit_in_turn
    kind = _kind_of(rec)
    dots = _dot_rows(rec)
    assert len(dots) == 60 and sum(k == "lasers" for k in kind.values()) == 60
    poses, rep = laser_curve_adjustment(rec, None, dots)
    it = rep["frames_in_turn"]
    lf = it["laser_frames"]
    print("\norbit in turn, true dot poses:", rep["reason"], rep["crossings"], rep["holdout"], it)
    # every laser frame placed but the last (no dot frame after it)
    assert lf["candidates"] == 60 and lf["placed"] == 59 and lf["left_out"] == {"at_the_ends": 1}, lf
    assert lf["used"] == 59 and lf["not_selected"] == 0 and lf["outside_the_trajectory"] == 0, lf
    assert lf["with_curves"] >= 50 and it["both_frames"] == 0 and it["left_out_kinds"] == {}
    assert it["placing"]["cubic"] + it["placing"]["straight"] == 59 and it["placing"]["cubic"] >= 55, it["placing"]
    assert it["dot_frames"] == {"given": 60, "tracked": 60, "in_solve": 60, "interval_s": pytest.approx(0.2, abs=1e-3)}
    # no laser line looked for in a dot frame
    assert sorted(read_frames) == sorted(i for i, k in kind.items() if k == "lasers")[:-1]
    assert rep["frames"] == 59 and rep["crossings"] >= CurveParams().min_crossings
    assert set(poses) == {r[0] for r in dots}
    # without the projector's dots the laser lines meet far better at the true poses (0.034 mm here; today's way, the
    # same orbit with both lights in every frame, 0.150), and the held-out test leaves the true poses alone (1.49)
    assert rep["miss_mm"]["before"] < 0.06, rep["miss_mm"]
    assert rep["reason"] == "not_better" and rep["holdout"]["ratio"] > CurveParams().holdout_max_ratio, rep["holdout"]
    assert rep["changed"] is False and rep["warning"] is None and "laser_poses" not in it
    assert all(np.array_equal(poses[i], T) for i, _, _, T in dots)


def test_in_turn_drift_is_reduced(orbit_in_turn):
    """Drift on the dot frames: mended as without kinds, the first frame kept, every dot frame given a pose. A laser
    frame has no pose of its own in the solve, it stays placed between the dot frames beside it: placed again between
    the new dot poses (as the frame pass will place it) it is where the solve had it."""
    rec = orbit_in_turn
    D = _drifted(_dot_rows(rec))
    poses, rep = laser_curve_adjustment(rec, None, D)
    g, it = rep["ground_truth"], rep["frames_in_turn"]
    print("\norbit in turn, drift:", rep["holdout"], rep["miss_mm"], g["before"]["rms_trans_mm"],
          g["after"]["rms_trans_mm"], it.get("laser_poses"))
    assert rep["reason"] == "adjusted" and rep["changed"] is True, rep["verdict"]
    assert g["after"]["rms_trans_mm"] < 0.8 * g["before"]["rms_trans_mm"]
    assert set(poses) == {r[0] for r in D} and np.array_equal(poses[D[0][0]], D[0][3])
    assert isinstance(rep["warning"], str) and rep["holdout"]["by"] == "frames"
    lp = it["laser_poses"]
    assert lp["solve_vs_placed_again"]["n"] == it["laser_frames"]["used"]
    assert lp["solve_vs_placed_again"]["mm_max"] < 1e-6 and lp["solve_vs_placed_again"]["deg_max"] < 1e-6, lp
    t = lp["ground_truth"]
    assert t["placed_again"]["trans_mm"]["median"] < 0.8 * t["before"]["trans_mm"]["median"], t


def test_in_turn_a_laser_frame_beside_an_untracked_or_cut_dot_frame_is_left_out(orbit_in_turn, tmp_path, read_frames):
    times = RecordingReader(orbit_in_turn).times()
    # a cut over dot frame 80 (the frame pass leaves it out of the trajectory) and one over laser frame 101 only
    cuts = [((times[79] + times[80]) / 2, (times[80] + times[81]) / 2),
            ((times[100] + times[101]) / 2, (times[101] + times[102]) / 2)]
    rec = _with_events(orbit_in_turn, tmp_path / "rec", [(float(times[110]), "cut", f"{a:.6f},{b:.6f}") for a, b in cuts])
    rows = [(i, t, i != 40, T) for i, t, ok, T in _dot_rows(rec) if i != 80]  # dot frame 40 not tracked
    poses, rep = laser_curve_adjustment(rec, None, rows, params=CurveParams(min_crossings=10 ** 9))
    lf = rep["frames_in_turn"]["laser_frames"]
    assert rep["reason"] == "few_crossings" and rep["changed"] is False
    assert lf["left_out"] == {"at_the_ends": 1, "untracked": 2, "cut": 3}, lf
    assert lf["candidates"] == 60 and lf["placed"] == 54 and lf["used"] == 54
    assert not {39, 41, 79, 81, 101, 119} & set(read_frames) and len(read_frames) == 54
    assert set(poses) == {r[0] for r in rows if r[2]}


def test_in_turn_no_laser_frame_placed_changes_nothing(orbit_in_turn, read_frames):
    dots = _dot_rows(orbit_in_turn)
    poses, rep = laser_curve_adjustment(orbit_in_turn, None, dots, laser_max_gap_s=0.01)
    assert rep["reason"] == "no_lasers" and rep["changed"] is False and "laser frame" in rep["verdict"]
    assert rep["frames_in_turn"]["laser_frames"]["left_out"] == {"gap": 59, "at_the_ends": 1}
    assert read_frames == [] and all(np.array_equal(poses[i], T) for i, _, _, T in dots)


def test_in_turn_both_frames_at_their_own_poses_mixed_and_dark_never_read(tmp_path, read_frames):
    """A ``both`` frame of an in-turn recording is a dot frame whose lines are read too, at its own pose, as today; a
    ``mixed`` or ``dark`` frame is never read."""
    shares = [(1.0, 1.0) if k % 4 == 0 else (1.0, 0.0) if k % 2 == 0 else (0.0, 1.0) for k in range(16)]
    shares[6] = (0.6, 0.4)  # the switch fell inside the exposure
    shares[9] = (0.0, 0.0)  # nothing lit
    rec = scan_recording(tmp_path / "rec", n_frames=16, fps=60.0, image_size=SIZE, realistic=True, seed=6,
                         depth_every=0, light=shares)
    kind = _kind_of(rec)
    assert [kind[i] for i in (0, 1, 2, 6, 9)] == ["both", "lasers", "dots", "mixed", "dark"]
    dots = _dot_rows(rec)
    assert [r[0] for r in dots] == [0, 2, 4, 8, 10, 12, 14]
    _, rep = laser_curve_adjustment(rec, None, dots, params=CurveParams(min_crossings=10 ** 9))
    it = rep["frames_in_turn"]
    assert sorted(read_frames) == [0, 1, 3, 4, 5, 7, 8, 11, 12, 13]
    assert it["both_frames"] == 4 and it["laser_frames"]["used"] == 6
    assert it["laser_frames"]["left_out"] == {"at_the_ends": 1} and it["left_out_kinds"] == {"mixed": 1, "dark": 1}


def test_a_tied_frame_moves_with_the_frames_it_lies_between():
    """``adjust_poses(tied=...)``: a crossing of a tied frame moves the frames it is placed between, and the tied frame
    is placed between their new poses. Two straight lines that miss by 1 mm: one of frame 0 (fixed), one of a frame tied
    half way between frames 0 and 2; frame 2 moves 2 mm so that the tied frame, half way, moves the 1 mm."""
    from ohs.track.curve_ba import Crossings, adjust_poses, crossing_residuals
    from ohs.track.pose_between import pose_between

    T = {0: np.eye(4), 1: np.eye(4), 2: np.eye(4)}
    one = lambda v: np.asarray([v], dtype=np.float64)  # noqa: E731
    # frame 0's line along x through the origin, the tied frame's line along y, 1 mm above it (z)
    cr = Crossings(np.array([0]), one([0.0, 0.0, 200.0]), one([1.0, 0.0, 0.0]), np.array([1]), one([0.0, 0.0, 201.0]),
                   one([0.0, 1.0, 0.0]))
    tied = {1: ((0, 2), (0.0, 2.0), 1.0)}
    p = CurveParams(odo_mm=100.0, odo_deg=100.0, iterations=5)
    T_new, info = adjust_poses(T, cr, [0, 1, 2], p, gaps={0: 2.0}, tied=tied)
    assert np.array_equal(T_new[0], np.eye(4))
    assert np.allclose(T_new[1], pose_between(1.0, (0.0, 2.0), [T_new[0], T_new[2]], base=0))
    assert abs(crossing_residuals(T_new, cr)[0][0]) < 1e-3
    assert np.linalg.norm(T_new[2][:3, 3]) == pytest.approx(2.0, abs=0.05)


def test_in_turn_max_frames_counts_the_laser_frames(orbit_in_turn, read_frames):
    """``max_frames`` spreads the laser frames read over the scan, as it spreads the frames without kinds; the solve
    holds the dot frames those lie between."""
    dots = _dot_rows(orbit_in_turn)
    poses, rep = laser_curve_adjustment(orbit_in_turn, None, dots, params=CurveParams(max_frames=20))
    it = rep["frames_in_turn"]
    assert rep["frames"] == 20 and len(read_frames) == 20 and len(set(read_frames)) == 20
    assert it["laser_frames"]["used"] == 20 and it["laser_frames"]["not_selected"] == 39
    assert 40 <= it["dot_frames"]["in_solve"] <= 4 * 20 + 1
    assert set(poses) == {r[0] for r in dots}
