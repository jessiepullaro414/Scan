"""Make mesh through the real server on a scan whose recording has frames in turn: ``dots`` frames and ``lasers``
frames (ENGINE_PLAN "Frames in turn without a wire"; the frame pass: ``tests/test_app_process_in_turn.py``, the laser
crossings: ``tests/test_track_curve_ba_in_turn.py``).

What each stage must do on such a scan:

* **Follow the camera:** ``trajectory.csv`` holds the dot frames only, ``laser_trajectory.csv`` the laser frames placed
  between them; both, and the report's ``frames_in_turn``, are in the stage's folder and in the scan's result;
* **Straighten:** the loops read the dot frames only (their depth); the fuse at the new poses places the laser frames
  again between the new dot poses; the laser crossings get the dot frames' trajectory and the frame pass's gap limit,
  and their ``frames_in_turn`` is carried in the stage's report;
* **Fuse:** at Straighten's poses, the laser frames placed again between them;
* **Refine with lasers:** reads the stage's laser cloud, which the laser frames filled (the dot frames give none).

The recording (a sphere on its plane, 24 frames at 60 a second, 640 x 400: 12 dot frames and 12 laser frames) is too
short for loops and has too few laser crossings to act on, so the loops' step and the crossings are run for real (to
see what they are given and read) and their poses are then moved by a known shift each, as loops that closed and
crossings that acted would move them; everything else runs for real. A scan without kinds is the other
server tests' (``tests/test_server_process_studio.py`` and the rest), unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from ohs.track.export import load_ply  # noqa: E402
from ohs.track.posegraph import read_trajectory  # noqa: E402
from recordings import scan_recording  # noqa: E402
from test_server_process_studio import add_scan, base_url, get, post, run_job, wait_job  # noqa: E402

SHIFT = np.array([0.6, -0.3, 0.2])  # mm, in the world: what the "loops" move the scan by
SHIFT_CROSSINGS = np.array([-0.2, 0.4, 0.1])  # mm, in the world: what the laser crossings move it by after them


def _rows(path: Path) -> list:
    return [(int(i), float(t), bool(ok), np.asarray(T, dtype=np.float64)) for i, t, ok, T in read_trajectory(path)]


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _placed_again(laser_rows: list, dot_rows: list, max_gap_s: float) -> dict[int, np.ndarray]:
    """The laser frames placed between ``dot_rows`` by the frame pass's rule."""
    from ohs.track.pose_between import place_laser_frames

    dots = sorted(dot_rows, key=lambda r: r[1])
    return {pl.index: pl.T_wc for pl in place_laser_frames([(i, t) for i, t, _, _ in laser_rows], dots,
                                                           max_gap_s=max_gap_s) if pl.T_wc is not None}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    import ohs.track.curve_ba as CB
    import ohs.track.posegraph as PG

    root = tmp_path_factory.mktemp("inturn")
    rec = scan_recording(root / "rec", n_frames=24, fps=60.0, image_size=(640, 400), realistic=True, seed=3,
                         depth_every=0, light="in_turn")
    reader = RecordingReader(rec)
    kind = {int(i): str(k) for i, k in zip(reader.indices, reader.kinds())}
    assert sorted(set(kind.values())) == ["dots", "lasers"]
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    seen: dict = {"depth_positions": [], "loops": [], "crossings": []}
    real_loops, real_crossings = PG.straighten_trajectory, CB.laser_curve_adjustment
    real_depth = PG.KeyframeClouds.depth

    def depth(self, pos):  # the frames whose depth the loops read
        seen["depth_positions"].append(int(pos))
        return real_depth(self, pos)

    def loops(rec_path, calib, trajectory, **kw):
        poses, rep = real_loops(rec_path, calib, trajectory, **kw)
        seen["loops"].append([int(r[0]) for r in trajectory])
        D = np.eye(4)
        D[:3, 3] = SHIFT
        moved = {int(i): D @ np.asarray(T, float) for i, T in poses.items()}
        return moved, {**rep, "changed": True, "loops_accepted": 1, "loops": [], "correction_mm": float(np.linalg.norm(SHIFT)),
                       "correction_deg": 0.0, "verdict": "Closed 1 loop"}

    def crossings(rec_path, calib, trajectory, **kw):
        seen["crossings"].append({"rows": [(int(r[0]), float(r[1]), bool(r[2]), np.asarray(r[3], float))
                                           for r in trajectory], "kw": dict(kw)})
        poses, rep = real_crossings(rec_path, calib, trajectory, **kw)
        seen["crossings"][-1]["report"] = rep
        # this short scan has too few crossings to act on: they are made to act (a known shift more), so the fuse after
        # them runs and must place the laser frames again between the dot frames' final poses
        D = np.eye(4)
        D[:3, 3] = SHIFT_CROSSINGS
        moved = {int(i): D @ np.asarray(T, float) for i, T in poses.items()}
        mm = float(np.linalg.norm(SHIFT_CROSSINGS))
        return moved, {**rep, "changed": True, "reason": "adjusted", "crossings": 50, "holdout": {"ratio": 0.5},
                       "miss_mm": {"before": 0.2, "after": 0.1}, "moved_mm": mm, "moved_deg": 0.0,
                       "warning": CB.acted_warning(mm)}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(PG.KeyframeClouds, "depth", depth)
        mp.setattr(PG, "straighten_trajectory", loops)
        mp.setattr(CB, "laser_curve_adjustment", crossings)
        with ServerThread(app) as srv:
            e = {"srv": srv, "url": srv.url, "root": root, "studio": app.state.studio, "rec": rec, "kind": kind,
                 "seen": seen, "positions": list(reader.indices)}
            e["pid"], e["sid"] = add_scan(e, "In turn", rec)
            b = base_url(e, e["pid"], e["sid"])
            r = post(b + "/process", {"quality": "preview"})
            assert r.status_code == 200, r.text
            done = wait_job(e, r.json()["id"], timeout=1500.0)
            assert done["state"] == "done", done
            e["b"], e["done"] = b, done
            e["dir"] = root / "projects" / e["pid"] / "scans" / e["sid"]
            yield e


def test_follow_the_camera_keeps_the_dot_frames_and_the_placed_laser_frames(env):
    d, kind = env["dir"] / "process", env["kind"]
    s = get(env["b"] + "/process").json()
    assert s["stages"]["track"]["state"] == "done", s["stages"]["track"]
    traj = _rows(d / "track" / "trajectory.csv")
    assert traj and all(kind[i] == "dots" for i, *_ in traj) and sum(ok for _, _, ok, _ in traj) >= 10, traj
    lt = _rows(d / "track" / "laser_trajectory.csv")
    assert len(lt) >= 10 and all(kind[i] == "lasers" for i, *_ in lt) and all(ok for _, _, ok, _ in lt)
    rep = _json(d / "track" / "report.json")
    it = rep["frames_in_turn"]
    assert it["kinds"] == {"dots": 12, "lasers": 12} and it["laser_frames"]["used"] == len(lt), it["laser_frames"]
    # the laser cloud is the laser frames' alone (no laser line is looked for in a dot frame)
    assert rep["lasers"]["n_points_raw"] == it["lasers"]["n_points_raw"] and sum(it["lasers"]["n_points_raw"].values())


def test_straighten_reads_dot_frames_places_the_laser_frames_again_and_gives_the_crossings_the_dot_path(env):
    d, kind, seen = env["dir"] / "process", env["kind"], env["seen"]
    track = _rows(d / "track" / "trajectory.csv")
    track_rep = _json(d / "track" / "report.json")
    gap = track_rep["frames_in_turn"]["placing"]["max_gap_s"]
    # the loops: given the dot frames; their keyframes (the frames whose depth they read) are dot frames, and so is
    # every frame whose depth they did read (none here: this short scan has no loop candidate to verify)
    assert seen["loops"] == [[i for i, *_ in track]]
    st = _json(d / "straighten.json")
    assert st["keyframe_frames"] and all(kind[int(i)] == "dots" for i in st["keyframe_frames"]), st["keyframe_frames"]
    assert all(kind[int(env["positions"][p])] == "dots" for p in seen["depth_positions"])
    # the laser crossings: the dot frames' trajectory (at the loops' poses) and the frame pass's gap limit
    assert len(seen["crossings"]) == 1
    call = seen["crossings"][0]
    assert [r[0] for r in call["rows"]] == [i for i, _, ok, _ in track if ok]
    assert all(kind[r[0]] == "dots" for r in call["rows"])
    assert call["kw"].get("laser_max_gap_s") == pytest.approx(gap), call["kw"]
    crep = call["report"]
    print("\nlaser crossings on the in-turn scan:", crep["reason"], crep["frames_in_turn"]["laser_frames"])
    assert crep["frames_in_turn"]["placing"]["max_gap_s"] == pytest.approx(gap)
    assert crep["frames_in_turn"]["laser_frames"]["placed"] > 0, crep["frames_in_turn"]
    assert st["laser_curves"]["frames_in_turn"] == json.loads(json.dumps(crep["frames_in_turn"]))
    stage = get(env["b"] + "/process").json()["stages"]["straighten"]
    assert stage["state"] == "done" and stage["crossings"] == 50, stage
    # the stage's mesh was fused at the final poses (the loops', then the crossings'): the laser frames placed again
    # between the final dot poses
    sd = d / "straighten"
    new = _rows(sd / "trajectory.csv")
    assert [r[0] for r in new] == [i for i, _, ok, _ in track if ok]
    track_by = {i: T for i, _, _, T in track}
    for i, _, _, T in new:
        assert np.allclose(T[:3, 3] - track_by[i][:3, 3], SHIFT + SHIFT_CROSSINGS, atol=1e-4), i
    lt_track, lt_new = _rows(d / "track" / "laser_trajectory.csv"), _rows(sd / "laser_trajectory.csv")
    assert [r[0] for r in lt_new] == [r[0] for r in lt_track]
    want = _placed_again(lt_track, new, gap)
    for i, _, _, T in lt_new:
        assert np.allclose(T, want[i], atol=1e-4), (i, T, want[i])
    old = {i: T for i, _, _, T in lt_track}
    assert all(np.linalg.norm(T[:3, 3] - old[i][:3, 3]) > 0.1 for i, _, _, T in lt_new)
    srep = _json(sd / "report.json")
    assert srep["poses"] == "given" and srep["frames_in_turn"]["laser_frames"]["used"] == len(lt_new)
    # the stage's report carries the crossings' account of the frames in turn
    assert srep["frames_in_turn"]["laser_crossings"] == st["laser_curves"]["frames_in_turn"]


def test_the_result_holds_the_base_stage_files_and_refine_used_the_laser_frames_points(env):
    d, res = env["dir"] / "process", env["dir"] / "result"
    s = get(env["b"] + "/process").json()
    base = s["base"]["stage"]
    rf = s["stages"]["refine"]
    print("\nrefine with lasers in Make mesh:", rf["state"], rf["summary"])
    assert rf["state"] in ("done", "skipped") and rf["source"] == "straighten", rf
    n_cloud = len(load_ply(d / "straighten" / "laser_points.ply")["vertices"])
    assert n_cloud >= 200 and rf["points"] >= 200, (n_cloud, rf)
    src = d / base
    for name in ("trajectory.csv", "laser_trajectory.csv", "laser_points.ply"):
        assert (res / name).is_file(), name
        assert (res / name).read_bytes() == (src / name).read_bytes(), name
    assert _json(res / "report.json")["frames_in_turn"]["laser_frames"]["used"] > 0


def test_fuse_and_refine_on_an_in_turn_scan(env):
    from ohs.mesh import vertex_normals
    from ohs.track.export import save_mesh

    d, b = env["dir"] / "process", env["b"]
    gap = _json(d / "track" / "report.json")["frames_in_turn"]["placing"]["max_gap_s"]
    run_job(env, post(b + "/process/fuse", {}), "process_fuse")
    s = get(b + "/process").json()
    assert s["stages"]["fuse"]["state"] == "done" and s["base"]["stage"] == "fuse"
    fd = d / "fuse"
    dots, straight = _rows(fd / "trajectory.csv"), _rows(d / "straighten" / "trajectory.csv")
    assert [r[0] for r in dots] == [r[0] for r in straight]
    assert all(np.allclose(a[3], c[3], atol=1e-4) for a, c in zip(dots, straight))
    lt = _rows(fd / "laser_trajectory.csv")
    want = _placed_again(_rows(d / "track" / "laser_trajectory.csv"), dots, gap)
    assert lt and all(np.allclose(T, want[i], atol=1e-4) for i, _, _, T in lt)
    assert (d.parent / "result" / "laser_trajectory.csv").read_bytes() == (fd / "laser_trajectory.csv").read_bytes()
    # Refine with lasers on the fused mesh moved 0.5 mm off its laser lines: pulled back onto the laser frames' points
    m = load_ply(fd / "mesh.ply")
    save_mesh(fd / "mesh.ply", m["vertices"] + 0.5 * vertex_normals(m["vertices"], m["faces"]), m["faces"],
              quality=m["quality"])
    run_job(env, post(b + "/process/refine"), "process_refine")
    rf = get(b + "/process").json()["stages"]["refine"]
    assert rf["state"] == "done" and rf["source"] == "fuse" and rf["points"] >= 200, rf
    assert rf["residual_after_mm"] < rf["residual_before_mm"], rf
    rd, res = d / "refine", d.parent / "result"
    for name in ("trajectory.csv", "laser_trajectory.csv", "laser_points.ply"):
        assert (rd / name).read_bytes() == (fd / name).read_bytes(), name
        assert (res / name).read_bytes() == (fd / name).read_bytes(), name
