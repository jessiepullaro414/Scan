"""``process_recording`` on a recording whose frames come in turn: dot frames and laser frames
(``ohs.app.process``, "Frames in turn"; ENGINE_PLAN "Frames in turn without a wire").

The dot frames are the frames the pass selects, tracks and fuses; no laser line is looked for in them. Each laser frame
gets its pose from the tracked dot frames beside it in time (``ohs.track.pose_between``) and adds its laser points to
the same laser cloud. ``trajectory.csv`` keeps the dot frames, ``laser_trajectory.csv`` the laser frames used, and
``report["frames_in_turn"]`` says what was done. A recording without kinds is processed as before
(``tests/test_app_process_unchanged.py``).

The standing walls (``tools/preset_study.py`` fins_scene: 0.5, 1 and 2 mm walls on a base) at 60 frames a second, in
turn and today's way (every frame both lights), measured against the scene's true surface; the numbers are printed.
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("open3d")

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from ohs.app import pipeline as pipeline_mod  # noqa: E402
from ohs.app.process import ProcessParams, process_recording  # noqa: E402
from ohs.geometry import invert_se3  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.track.posegraph import read_trajectory  # noqa: E402
from ohs.types import TrackResult  # noqa: E402
from recordings import cached_recording, scan_recording  # noqa: E402

SIZE = (640, 400)


def _walls(out, seed: int, n_frames: int, light):
    """The standing walls at 60 frames a second (in turn: 30 dot frames a second), 640x400."""
    from ohs.sim.datasets import make_scan_recording  # noqa: PLC0415
    from tools.preset_study import fins_scene  # noqa: PLC0415

    make_scan_recording(out, scene=fins_scene(seed), n_frames=n_frames, fps=60.0, image_size=SIZE, realistic=True,
                        depth_every=0, seed=seed, overwrite=True, light=light)


def _sphere(path: Path, n_frames: int = 24, light="in_turn", seed: int = 3) -> Path:
    return scan_recording(path, n_frames=n_frames, fps=60.0, image_size=SIZE, realistic=True, seed=seed,
                          depth_every=0, light=light)


def _with_events(src: Path, dst: Path, rows: list[tuple[float, str, str]]) -> Path:
    shutil.copytree(src, dst)
    with open(dst / "events.csv", "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for t, kind, value in rows:
            w.writerow([f"{t:.6f}", kind, value])
    return dst


def _rows(path: Path) -> list[tuple[int, float, bool, np.ndarray]]:
    return [(int(i), float(t), bool(ok), np.asarray(T)) for i, t, ok, T in read_trajectory(path)]


def off_surface(out: Path, rec: Path, scene) -> np.ndarray:
    """|distance| (mm) of the written laser points to the scene's true surface, the estimated world put on the true
    one at the first tracked dot frame (as ``trajectory_errors`` does)."""
    import open3d as o3d  # noqa: PLC0415
    from tools.calib_health_study import Truth  # noqa: PLC0415

    gt = RecordingReader(rec).ground_truth_poses()
    i0, _, _, T0 = next(r for r in _rows(out / "trajectory.csv") if r[2])
    A = gt[i0] @ invert_se3(T0)
    P = np.asarray(o3d.io.read_point_cloud(str(out / "laser_points.ply")).points, dtype=np.float64)
    X = P @ A[:3, :3].T + A[:3, 3]
    return np.abs(Truth(scene).sdf(X))


def _count_line_searches(monkeypatch) -> list[int]:
    """Counts every laser line search of the pipeline (one per image: left and right)."""
    calls: list[int] = []
    real = pipeline_mod.extract_laser_lines

    def counted(img, params=None):
        calls.append(int(np.asarray(img).sum()))
        return real(img, params)

    monkeypatch.setattr(pipeline_mod, "extract_laser_lines", counted)
    return calls


# --------------------------------------------------------------------------- the standing walls
@pytest.fixture(scope="module")
def walls(tmp_path_factory):
    base = tmp_path_factory.mktemp("walls")
    return {"turn": cached_recording(base / "turn", _walls, seed=0, n_frames=48, light="in_turn"),
            "today": cached_recording(base / "today", _walls, seed=0, n_frames=48, light=None)}


@pytest.mark.slow
def test_the_standing_walls_in_turn(walls, tmp_path, monkeypatch):
    from tools.preset_study import fins_scene  # noqa: PLC0415

    calls = _count_line_searches(monkeypatch)
    out = tmp_path / "turn"
    rep = process_recording(walls["turn"], out)
    monkeypatch.undo()
    fr, it = rep["frames"], rep["frames_in_turn"]
    # the dot frames are the pass's frames: every even one, tracked; no laser line looked for in them
    assert fr["processed"] == 24 and fr["tracked"] == 24, fr
    traj = _rows(out / "trajectory.csv")
    assert [r[0] for r in traj] == list(range(0, 48, 2)) and all(r[2] for r in traj)
    assert not any("n_laser" in r for r in rep["per_frame"])
    assert rep["lasers"]["n_rejected_by_depth"] == {}
    # the laser frames: all placed but the last (no dot frame after it); two searches (left, right) each
    assert it["kinds"] == {"dots": 24, "lasers": 24}
    lf = it["laser_frames"]
    assert lf["candidates"] == 24 and lf["used"] == 23 and lf["left_out"] == {"at_the_ends": 1}, lf
    assert len(calls) == 2 * 24
    assert it["placing"]["cubic"] == 21 and it["placing"]["straight"] == 2, it["placing"]
    lt = _rows(out / "laser_trajectory.csv")
    assert [r[0] for r in lt] == list(range(1, 46, 2)) and all(r[2] for r in lt)
    assert rep["files"]["laser_trajectory"] == str(out / "laser_trajectory.csv")
    assert rep["lasers"]["frames_with_points"] == it["lasers"]["frames_with_points"] > 20
    assert it["ground_truth"]["n"] == 23
    # (tracked, these 48 frames at 640 px slide by 20 to 30 mm on the processor path, today's way as much: so the
    # laser points are measured against the truth with the dot frames at their true poses, below)
    print("\nstanding walls, tracked: the dot frames' path against the truth", rep["ground_truth"]["trans_mm"])

    # at the true poses of the dot frames (given, as Straighten gives its poses): the laser frames are placed between
    # them; today's way every frame both lights at its true pose, all 48 and the even ones only (as many laser frames)
    gt = RecordingReader(walls["turn"]).ground_truth_poses()
    dots_gt = {i: T for i, T in gt.items() if i % 2 == 0}
    turn = process_recording(walls["turn"], tmp_path / "turn_true", poses=dots_gt)
    today = process_recording(walls["today"], tmp_path / "today_true", poses=gt)
    even = process_recording(walls["today"], tmp_path / "even_true", poses=dots_gt)
    assert "frames_in_turn" not in today and today["frames"]["processed"] == 48 and even["frames"]["processed"] == 24
    g = turn["frames_in_turn"]["ground_truth"]
    assert g["n"] == 23 and g["trans_mm"]["median"] < 0.04 and g["rot_deg"]["median"] < 0.02, g
    sc = fins_scene(0)
    d = {k: off_surface(tmp_path / f"{k}_true", walls["turn" if k == "turn" else "today"], sc)
         for k in ("turn", "today", "even")}
    stats = {k: (len(v), round(float(np.median(v)), 4), round(float(np.percentile(v, 90)), 4)) for k, v in d.items()}
    print("standing walls at true dot poses, laser points off the true surface (points, median mm, nine in ten within "
          "mm):", stats)
    print("placed laser poses against the truth:", g["trans_mm"], g["rot_deg"])
    # the laser points of the frames in turn lie at least as close to the true surface as today's
    assert stats["turn"][1] <= stats["today"][1] and stats["turn"][2] <= stats["today"][2], stats
    assert stats["turn"][1] <= stats["even"][1] and stats["turn"][2] <= stats["even"][2], stats


@pytest.mark.slow
def test_a_tracked_sphere_in_turn_against_todays_way(tmp_path):
    """Tracked (the sphere on its plane tracks to about 0.3 mm here): the laser points of the frames in turn lie at
    least as close to the true surface as today's, and the mesh of the dot frames is about today's mesh of as many
    frames."""
    from ohs.sim.scene import make_scene  # noqa: PLC0415

    turn_rec = _sphere(tmp_path / "turn", n_frames=48, seed=0)
    today_rec = _sphere(tmp_path / "today", n_frames=48, light=None, seed=0)
    turn = process_recording(turn_rec, tmp_path / "turn_out")
    today = process_recording(today_rec, tmp_path / "today_out")
    assert turn["frames"]["tracked"] == 24 and today["frames"]["tracked"] == 48
    assert turn["ground_truth"]["trans_mm"]["median"] < 1.0
    assert turn["frames_in_turn"]["laser_frames"]["used"] == 23
    sc = make_scene("sphere_on_plane", 0)
    a, b = off_surface(tmp_path / "turn_out", turn_rec, sc), off_surface(tmp_path / "today_out", today_rec, sc)
    print("\nsphere tracked, laser points off the true surface: in turn median %.4f p90 %.4f (%d points); today "
          "median %.4f p90 %.4f (%d)" % (np.median(a), np.percentile(a, 90), len(a), np.median(b),
                                         np.percentile(b, 90), len(b)))
    assert np.median(a) <= np.median(b) and np.percentile(a, 90) <= np.percentile(b, 90)


# --------------------------------------------------------------------------- the rules, on a small sphere
@pytest.fixture(scope="module")
def sphere(tmp_path_factory) -> Path:
    return _sphere(tmp_path_factory.mktemp("sphere") / "rec")


def test_a_sphere_in_turn_and_the_rate_rule(sphere, tmp_path):
    """60 frames a second in turn is 30 dot frames a second: Standard's cap of 30 keeps every dot frame."""
    rep = process_recording(sphere, tmp_path / "out", params=ProcessParams(max_frame_rate=30.0))
    fr, it = rep["frames"], rep["frames_in_turn"]
    assert fr["step"] == 1 and fr["recorded_fps"] == pytest.approx(30.0, rel=0.02), fr
    assert fr["processed"] == 12 and fr["tracked"] == 12
    assert it["laser_frames"]["used"] == 11 and it["laser_frames"]["left_out"] == {"at_the_ends": 1}
    assert it["laser_frames"]["recorded_fps"] == pytest.approx(30.0, rel=0.02)
    # the depth check a laser frame has no depth for: by the neighbouring dot frames' depth, counted and not applied
    chk = it["lasers"]["depth_check"]
    assert chk["applied"] is False and chk["checked"] > 0 and "would_drop" in chk
    assert rep["lasers"]["n_points_raw"] == it["lasers"]["n_points_raw"]  # only laser frames gave laser points
    # with the check applied, the laser cloud loses exactly what it would drop
    on = process_recording(sphere, tmp_path / "on", params=ProcessParams(max_frame_rate=30.0, laser_frame_depth_check=True))
    c2 = on["frames_in_turn"]["lasers"]["depth_check"]
    assert c2["applied"] is True and c2["would_drop"] == chk["would_drop"]
    assert (sum(on["lasers"]["n_points_raw"].values())
            == sum(rep["lasers"]["n_points_raw"].values()) - sum(chk["would_drop"].values()))
    # a recording of 60 frames a second today's way at the same cap: every second frame, as before
    today = _sphere(tmp_path / "today", light=None)
    t = process_recording(today, tmp_path / "t", params=ProcessParams(max_frame_rate=30.0, stop=6, health=False))
    assert t["frames"]["step"] == 2 and "frames_in_turn" not in t
    assert not (tmp_path / "t" / "laser_trajectory.csv").exists()


@pytest.mark.parametrize("rule", ["picked_by_motion", "rate_cap"])
def test_a_fuse_at_the_first_pass_poses_uses_the_laser_frames_it_used(sphere, tmp_path, rule):
    """Straighten and Fuse run the frame pass again at the first pass's poses. The dot frames with a pose are fused
    (no new pick, no rate cap); the laser frames and their gap limit must follow the first pass's rule, or the laser
    frames it used are left out as "gap" and the laser cloud is empty after Straighten."""
    p = (ProcessParams(frame_motion_deg=30.0, frame_max_gap=8, health=False) if rule == "picked_by_motion"
         else ProcessParams(max_frame_rate=10.0, health=False))  # 30 dot frames a second: every third
    first = process_recording(sphere, tmp_path / "first", params=p)
    a = first["frames_in_turn"]
    assert first["frames"]["selection"]["by"] == ("motion" if rule == "picked_by_motion" else "step")
    assert first["frames"]["processed"] < 12 and a["laser_frames"]["used"] >= 2, (first["frames"], a["laser_frames"])
    poses = {i: T for i, _, ok, T in _rows(tmp_path / "first" / "trajectory.csv") if ok}
    again = process_recording(sphere, tmp_path / "again", params=p, poses=poses)
    b = again["frames_in_turn"]
    assert b["placing"]["max_gap_s"] == pytest.approx(a["placing"]["max_gap_s"]), (a["placing"], b["placing"])
    assert b["laser_frames"]["used"] == a["laser_frames"]["used"], (a["laser_frames"], b["laser_frames"])
    la, lb = _rows(tmp_path / "first" / "laser_trajectory.csv"), _rows(tmp_path / "again" / "laser_trajectory.csv")
    assert [r[0] for r in lb] == [r[0] for r in la]
    assert all(np.allclose(x[3], y[3], atol=1e-9) for x, y in zip(la, lb))  # the same poses: placed the same
    assert sum(again["lasers"]["n_points_raw"].values()) == sum(first["lasers"]["n_points_raw"].values()) > 0


def test_a_laser_frame_beside_a_lost_or_cut_dot_frame_is_left_out(sphere, tmp_path, monkeypatch):
    times = RecordingReader(sphere).times()
    # a cut over dot frame 12 only (half way to its neighbours)
    a, b = (times[11] + times[12]) / 2, (times[12] + times[13]) / 2
    rec = _with_events(sphere, tmp_path / "rec", [(float(times[13]), "cut", f"{a:.6f},{b:.6f}")])
    real = pipeline_mod.FramePipeline.process

    def lose_frame_6(self, frame, *args, **kw):
        res = real(self, frame, *args, **kw)
        if int(frame.index) == 6:
            res.track = TrackResult(res.track.T_wc, False, {"reason": "test"})
        return res

    monkeypatch.setattr(pipeline_mod.FramePipeline, "process", lose_frame_6)
    rep = process_recording(rec, tmp_path / "out", params=ProcessParams(health=False))
    lf = rep["frames_in_turn"]["laser_frames"]
    assert rep["frames"]["cut"] == 1 and rep["frames"]["processed"] == 11
    assert lf["left_out"] == {"untracked": 2, "cut": 2, "at_the_ends": 1}, lf
    assert lf["used"] == 12 - 5
    used = [r[0] for r in _rows(tmp_path / "out" / "laser_trajectory.csv")]
    assert used == [1, 3, 9, 15, 17, 19, 21]
    traj = _rows(tmp_path / "out" / "trajectory.csv")
    assert [r[0] for r in traj] == [0, 2, 4, 6, 8, 10, 14, 16, 18, 20, 22] and not traj[3][2]


def test_mixed_and_dark_frames_are_left_out_and_counted(tmp_path, monkeypatch):
    shares = [(1.0, 0.0) if k % 2 == 0 else (0.0, 1.0) for k in range(16)]
    shares[4] = (0.6, 0.4)  # the switch fell inside the exposure
    shares[9] = (0.0, 0.0)  # nothing lit
    rec = _sphere(tmp_path / "rec", n_frames=16, light=shares, seed=5)
    assert list(RecordingReader(rec).kinds())[3:10] == ["lasers", "mixed", "lasers", "dots", "lasers", "dots", "dark"]
    calls = _count_line_searches(monkeypatch)
    rep = process_recording(rec, tmp_path / "out", params=ProcessParams(health=False))
    it = rep["frames_in_turn"]
    assert it["kinds"] == {"dots": 7, "lasers": 7, "mixed": 1, "dark": 1}
    assert it["left_out_kinds"] == {"mixed": 1, "dark": 1}
    assert rep["frames"]["processed"] == 7 and it["laser_frames"]["candidates"] == 7
    assert len(calls) == 2 * 7  # in laser frames only
    lf = it["laser_frames"]
    assert lf["used"] + sum(lf["left_out"].values()) == 7
    idx = [r[0] for r in _rows(tmp_path / "out" / "trajectory.csv")]
    assert 4 not in idx and 9 not in idx and idx == [0, 2, 6, 8, 10, 12, 14]


def test_frames_holding_both_lights_in_a_recording_with_kinds(tmp_path):
    """A ``both`` frame is a dot frame that also gives its laser points, at its own tracked pose, as today."""
    shares = [(1.0, 1.0) if k % 4 == 0 else (1.0, 0.0) if k % 2 == 0 else (0.0, 1.0) for k in range(12)]
    rec = _sphere(tmp_path / "rec", n_frames=12, light=shares, seed=6)
    rep = process_recording(rec, tmp_path / "out", params=ProcessParams(health=False))
    with_points = [r["index"] for r in rep["per_frame"] if r.get("n_laser")]
    assert with_points == [0, 4, 8]
    assert rep["frames_in_turn"]["kinds"] == {"both": 3, "dots": 3, "lasers": 6}
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["frames_in_turn"]["laser_frames"]["used"] == 5


def test_laser_frames_let_go_of_the_dot_frames_depth():
    """A laser frame is checked against the depth of the dot frames beside it. Once that is done it must let go
    of them: held until the end of the pass, a long scan would keep every dot frame's depth map in memory."""
    import gc
    import weakref
    from types import SimpleNamespace

    from ohs.app.in_turn import LaserFrames

    pipe = SimpleNamespace(
        K=np.array([[300.0, 0, 160], [0, 300.0, 100], [0, 0, 1]]),
        laser_params=SimpleNamespace(depth_check_k=4.0, depth_check_min_mm=1.0),
        laser_frame=lambda f: SimpleNamespace(index=f.index, t=f.t_host, laser_points={"vertical": np.zeros((0, 3))},
                                              timings_ms={}))
    lf = LaserFrames(pipe)
    alive = []
    for k in range(4):
        depth = np.full((200, 320), 250.0, np.float32)
        alive.append(weakref.ref(depth))
        lf.dot_frame(SimpleNamespace(t=2.0 * k, track=SimpleNamespace(T_wc=np.eye(4), ok=True), depth=depth, sigma=None))
        del depth
        lf.laser_frame(SimpleNamespace(index=100 + k, t_host=2.0 * k + 1.0))
    gc.collect()
    # the last dot frame's depth is still needed (the laser frame after it waits for the next dot frame)
    assert [r() is not None for r in alive] == [False, False, False, True]
