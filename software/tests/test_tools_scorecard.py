"""tools/scorecard.py (ENGINE_PLAN X0): the part placed alone, the verdicts, "not measured", the paired replay.

Fast: synthetic points on a box standing on a table (no rendering), and one tiny 3-frame recording
at 640x400 for the replay (from the test recordings' cache)."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))

from ohs import geometry as G  # noqa: E402
from tools import scorecard as sc  # noqa: E402

HALF = np.array([20.0, 12.0, 8.0])  # the box's half extents; it stands on the table (z = 0)
CENTRE = np.array([3.0, -2.0, 8.0])


def _scene():
    from ohs.sim.scene import Box, Rect

    from tools.calib_health_study import Truth

    table = Rect(None, (-300.0, 300.0, -300.0, 300.0), 0.5, name="table")
    box = Box(G.make_se3(G.so3_exp(np.array([0.0, 0.0, 0.3])), CENTRE), HALF, 0.6, name="part")
    t = Truth(type("S", (), {"primitives": [table, box]})())
    return box, sc.SdfTruth(t.prim_sdf, [0])


def _points(box, grow: float = 0.0, h: float = 1.0):
    """Points on the box's five visible faces (its four sides pushed out by ``grow`` mm along their
    normals: no rigid move can take that away) and on the table round it."""
    P = []
    for ax in range(3):
        o = [k for k in range(3) if k != ax]
        u, v = np.meshgrid(np.arange(-HALF[o[0]] + 0.5, HALF[o[0]], h), np.arange(-HALF[o[1]] + 0.5, HALF[o[1]], h))
        for s in (-1.0, 1.0):
            if ax == 2 and s < 0:
                continue  # the face on the table
            X = np.zeros((u.size, 3))
            X[:, o[0]], X[:, o[1]], X[:, ax] = u.ravel(), v.ravel(), s * (HALF[ax] + (grow if ax < 2 else 0.0))
            P.append(X)
    part = G.transform_points(box.T_wo, np.concatenate(P))
    x, y = np.meshgrid(np.arange(-80.0, 80.0, 2.0), np.arange(-80.0, 80.0, 2.0))
    T = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)])
    loc = (T - box.t) @ box.R
    T = T[~np.all(np.abs(loc[:, :2]) < HALF[:2] + 1.0, axis=1)]
    return part, T


def _err(deg=0.5, mm=1.0):
    ax = np.array([0.3, -0.5, 0.8])
    ax /= np.linalg.norm(ax)
    return G.make_se3(G.so3_exp(np.radians(deg) * ax), mm * np.array([0.6, 0.0, -0.8]))


def test_a_perfect_mesh_scores_zero():
    box, truth = _scene()
    part, table = _points(box)
    V = np.vstack([part, table])
    T, _ = sc.place_part(V, np.eye(4), truth)
    s = sc.part_scores(G.transform_points(T, V), truth)
    assert s["part_n"] == len(part)
    assert s["part_med"] < 1e-6 and s["part_p95"] < 1e-6 and abs(s["part_signed"]) < 1e-6 and s["part_over03"] == 0.0


def test_a_rigid_error_is_found_and_scores_zero():
    box, truth = _scene()
    part, table = _points(box)
    Te = _err()
    V = G.transform_points(G.invert_se3(Te), np.vstack([part, table]))
    T, _ = sc.place_part(V, np.eye(4), truth)
    Vs = G.transform_points(T, V)
    assert np.sqrt(np.mean(np.sum((Vs[:len(part)] - part) ** 2, axis=1))) < 1e-4  # the part is back on its place
    assert sc.part_scores(Vs, truth)["part_p95"] < 1e-4


def test_a_mesh_off_by_a_known_amount_scores_that_amount():
    box, truth = _scene()
    part, table = _points(box, grow=0.2)  # the four sides 0.2 mm out, the top true
    V = np.vstack([part, table])
    T, _ = sc.place_part(V, G.invert_se3(_err(0.2, 0.4)), truth)
    Vs = G.transform_points(T, V)
    side = np.abs(((part - box.t) @ box.R)[:, 2]) < HALF[2] - 0.01
    s_side = sc.part_scores(Vs[:len(part)][side], truth)
    s_top = sc.part_scores(Vs[:len(part)][~side], truth)
    assert s_side["part_med"] == pytest.approx(0.2, abs=1e-4) and s_side["part_signed"] == pytest.approx(0.2, abs=1e-4)  # + outside
    assert s_top["part_p95"] < 1e-4
    s = sc.part_scores(Vs, truth)
    assert s["part_signed"] == pytest.approx(0.2 * side.mean(), abs=1e-4) and s["part_over03"] == 0.0


def test_the_table_does_not_pull_the_part():
    """A table left 1 mm low (as tracking can leave it): the part alone is placed exactly; the whole
    scene's placement (``calib_health_study.place``, what the studies used) is pulled off by it."""
    from tools.calib_health_study import Truth, place

    box, truth = _scene()
    part, table = _points(box)
    table = table - np.array([0.0, 0.0, 1.0])
    V = np.vstack([part, table])
    T, _ = sc.place_part(V, np.eye(4), truth)
    s = sc.part_scores(G.transform_points(T, V), truth)
    assert s["part_p95"] < 1e-4
    from ohs.sim.scene import Rect

    whole = Truth(type("S", (), {"primitives": [Rect(None, (-300.0, 300.0, -300.0, 300.0), 0.5, name="table"), box]})())
    Tw = place(V, np.eye(4), whole)
    sw = sc.part_scores(G.transform_points(Tw, V), truth)
    assert sw["part_p95"] > 0.3  # the table rules a whole-scene fit: the box's top sinks with it


def test_the_placement_goes_by_position_not_by_vertex_number():
    box, truth = _scene()
    part, table = _points(box)
    V = G.transform_points(G.invert_se3(_err()), np.vstack([part, table])) + 0.01 * np.sin(np.arange(len(part) + len(table)))[:, None]
    perm = np.random.default_rng(3).permutation(len(V))
    T1, _ = sc.place_part(V, np.eye(4), truth)
    T2, _ = sc.place_part(V[perm], np.eye(4), truth)
    assert np.array_equal(T1, T2)
    assert np.array_equal(sc.position_sample(V, 500), sc.position_sample(V[perm], 500))


def test_a_sheet_with_fill_beyond_its_free_edge_stays_put():
    """A thin sheet (an open surface, like the car panel) scanned with fill hanging past two of its
    free edges, as the matcher leaves it over a deep drop: the placement must not slide the sheet
    along itself to pull that fill in (it slid the car panel 8 to 13 mm, even at true poses); the
    fill still counts as error, and its share is reported."""
    xs = np.arange(-60.0, 60.01, 1.0)
    X, Y = np.meshgrid(xs, xs * 0.7)
    Z = 0.002 * X ** 2 + 0.003 * Y ** 2 + 120.0  # doubly curved, as the car panel
    V = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
    nx = len(xs)
    i, j = np.meshgrid(np.arange(len(xs) - 1), np.arange(nx - 1), indexing="ij")
    a = (i * nx + j).ravel()
    F = np.concatenate([np.stack([a, a + 1, a + nx + 1], 1), np.stack([a, a + nx + 1, a + nx], 1)])
    truth = sc.MeshTruth(V, F, floor_z=0.0)
    # the scan: the sheet itself, and a skirt of fill out to 12 mm past the +x and +y edges, sagging
    sheet = V + np.array([0.0, 0.0, 0.0])
    sx, sz = np.meshgrid(np.arange(60.5, 72.0, 0.5), np.arange(-40.0, 40.0, 1.0))
    skirt_x = np.column_stack([sx.ravel(), sz.ravel(), 0.002 * 60.0 ** 2 + 0.003 * sz.ravel() ** 2 + 120.0 - 0.3 * (sx.ravel() - 60.0)])
    sy, sw = np.meshgrid(np.arange(42.5, 54.0, 0.5), np.arange(-55.0, 55.0, 1.0))
    skirt_y = np.column_stack([sw.ravel(), sy.ravel(), 0.002 * sw.ravel() ** 2 + 0.003 * 42.0 ** 2 + 120.0 - 0.3 * (sy.ravel() - 42.0)])
    scan = np.vstack([sheet, skirt_x, skirt_y])
    T, info = sc.place_part(scan, np.eye(4), truth)
    assert np.linalg.norm(T[:3, 3]) < 0.01 and G.rotation_angle_deg(T[:3, :3]) < 0.01, info
    s = sc.part_scores(G.transform_points(T, scan), truth)
    assert s["part_beyond_edge"] == pytest.approx((len(skirt_x) + len(skirt_y)) / len(scan), abs=0.02)
    assert s["part_p95"] > 1.0  # the fill is still error


def test_a_gently_curved_sheet_is_not_slid_by_a_bias(monkeypatch):
    """A car-panel-like sheet (radii about 900 and 1400 mm) at its true place, with one region of it
    fused 0.3 mm high: a best fit along the sheet's own face is hardly fixed, and that bias slid it
    millimetres (as on the car panel at true poses). The placement leaves such weak directions where
    they are; with the rule off (``WEAK = 0``) the same data slides it."""
    xs = np.arange(-150.0, 150.01, 2.0)
    ys = np.arange(-100.0, 100.01, 2.0)
    X, Y = np.meshgrid(xs, ys)
    Z = X ** 2 / 1800.0 + Y ** 2 / 2800.0 + 120.0
    V = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
    nx = len(xs)
    i, j = np.meshgrid(np.arange(len(ys) - 1), np.arange(nx - 1), indexing="ij")
    a = (i * nx + j).ravel()
    F = np.concatenate([np.stack([a, a + 1, a + nx + 1], 1), np.stack([a, a + nx + 1, a + nx], 1)])
    truth = sc.MeshTruth(V, F, floor_z=0.0)
    scan = V.copy()
    bump = (V[:, 0] > 60.0) & (np.abs(V[:, 1]) < 60.0)
    scan[bump, 2] += 0.3
    T, info = sc.place_part(scan, np.eye(4), truth)
    assert info["weak_directions"] >= 2
    def slide(T):  # how far the sheet's points moved along it (x, y), in the mean
        return float(np.mean(np.linalg.norm((G.transform_points(T, V) - V)[:, :2], axis=1)))

    assert slide(T) < 0.05, (slide(T), info)  # not slid along the sheet
    monkeypatch.setattr(sc, "WEAK", 0.0)
    T0, _ = sc.place_part(scan, np.eye(4), truth)
    assert slide(T0) > 0.5, slide(T0)  # without the rule the bias slides it


def test_verdicts_against_a_made_up_spread():
    v = sc.verdict
    assert v([1.0, 1.1, 0.9], [0.5, 0.6, 0.45], 0.2, "min")[0] == "better"
    assert v([1.0, 1.1, 0.9], [1.5, 1.6, 1.45], 0.2, "min")[0] == "worse"
    assert v([1.0, 1.1, 0.9], [0.95, 1.0, 0.88], 0.2, "min")[0] == "same"  # inside the spread
    assert v([1.0, 1.1, 0.9], [0.5, 0.6, 1.0], 0.2, "min")[0] == "same"  # one recording went the other way
    assert v([1.0, 1.1, 0.9], [1.0, 1.1, 0.9], 0.2, "min")[0] == "identical"
    assert v([0.90, 0.92], [0.99, 0.99], 0.02, "max")[0] == "better"
    assert v([-0.5, -0.6], [0.1, -0.1], 0.1, "zero")[0] == "better"  # nearer 0, whatever the sign
    assert v([0.1, 0.12], [0.5, -0.5], 0.02, "zero")[0] == "worse"
    assert v([1.0], [0.5], None, "min")[0] == sc.NO_SPREAD  # one recording: neither passes nor fails
    assert v([1.0, 2.0], [5.0, 9.0], 1.0, "info")[0] == "info"
    assert v([None, None], [1.0, 2.0], None, "min")[0] == sc.NM
    assert sc.spread([1.0, None, 3.0]) == 2.0 and sc.spread([1.0, None]) is None


def test_not_measured_is_never_zero():
    assert sc.fmt(None) == sc.NM and sc.fmt(float("nan")) == sc.NM and sc.fmt(0.0) == "0.0000"
    spec = sc.SCENES["fins-s0"]
    res = {"fins-s0": {"tracked": {"metrics": {"wall0.5_err": None, "part_med": 0.0, "cover1": 0.9}}}}
    text = "\n".join(sc.table(res, [spec]))
    assert "| wall0.5_err | n/m | n/m |" in text and "| part_med | 0.0000 |" in text
    base = {"fins-s0": {"tracked": {"metrics": {"wall0.5_err": 1.2, "part_med": 0.1}}}}
    rows = {r["metric"]: r for r in sc.compare_rows(base, res, [spec])}
    assert rows["wall0.5_err"]["verdict"] == sc.NM and rows["wall0.5_err"]["flag"] == "LOST"
    assert rows["wall0.5_err"]["thin"]


def test_thin_parts_raise_their_own_alarm():
    assert sc.thin_flag("wall0.5_err", [1.2, 1.21], [1.2, 1.3]) == "THICKER"
    assert sc.thin_flag("wall0.5_cover05", [0.9, 0.9], [0.9, 0.5]) == "FACE LOST?"
    assert sc.thin_flag("wall1_err", [0.7, 0.7], [0.71, 0.69]) == ""
    # found on the no-GPU plates (X0a): a plate read 0.39 mm too thin, then 0.03 mm too thin, and the alarm said
    # "THICKER". Thicker towards the truth is no alarm; thicker past the truth by more than the limit is
    assert sc.thin_flag("plate_err", [-0.387, -0.228], [-0.031, -0.044]) == "LESS THIN"
    assert "LESS THIN" not in sc.WORSE_FLAGS
    assert sc.thin_flag("plate_err", [-0.03], [0.10]) == "THICKER"
    assert sc.thin_flag("plate_err", [-0.03], [0.06]) == "LESS THIN"  # 0.03 mm further off: under the limit
    spec = [sc.SCENES["fins-s0"], sc.SCENES["fins-s1"]]
    base = {n: {"tracked": {"metrics": {"wall1_err": 0.7, "part_med": 0.1}}} for n in ("fins-s0", "fins-s1")}
    new = {n: {"tracked": {"metrics": {"wall1_err": 0.8, "part_med": 0.1}}} for n in ("fins-s0", "fins-s1")}
    text = "\n".join(sc.compare_table(sc.compare_rows(base, new, spec)))
    assert text.index("THIN PARTS") < text.index("Worse") and "THICKER" in text and "1 ALARM" in text
    assert "Thin parts worse: fins (tracked: wall1_err THICKER)" in text and "Better or the same" not in text


def test_the_summary_line():
    specs = [sc.SCENES["bracket240-s0"], sc.SCENES["bracket240-s1"], sc.SCENES["e2e-panel"]]
    names = [s.name for s in specs]
    base = {n: {"tracked": {"metrics": {"part_med": 0.10 + 0.01 * k}}} for k, n in enumerate(names)}
    same = {n: {"tracked": {"metrics": {"part_med": 0.10 + 0.01 * k + 0.001}}} for k, n in enumerate(names[:2])}
    same[names[2]] = base[names[2]]
    assert sc.summary_line(sc.compare_rows(base, same, specs)) == "**Better or the same on every scene.**"
    worse = {n: {"tracked": {"metrics": {"part_med": 0.5}}} for n in names[:2]}
    worse[names[2]] = {"tracked": {"metrics": {"part_med": 0.3}}}
    line = sc.summary_line(sc.compare_rows(base, worse, specs))
    assert "Worse on: bracket240 (tracked)" in line and "e2e-panel" in line and "not counted as passing" in line


def test_settings_to_try():
    p = sc.parse_params(["fusion.voxel_mm=0.5", "pipeline.thin_walls=off", "pipeline.gap_guard_px=4"])
    assert p == {"fusion.voxel_mm": 0.5, "pipeline.thin_walls": "off", "pipeline.gap_guard_px": 4}
    assert sc.settings_key({}) == "default" and sc.settings_key(p) == sc.settings_key(dict(reversed(list(p.items()))))
    with pytest.raises(ValueError):
        sc.parse_params(["voxel_mm=0.5"])
    pp = sc.apply_params(sc.base_params(sc.SCENES["fins-s0"]), p)
    assert pp.pipeline.fusion.voxel_mm == 0.5 and pp.pipeline.thin_walls == "off" and pp.pipeline.gap_guard_px == 4


def test_the_scenes_truths():
    assert sc.holes_plate_sdf("low", G.transform_points(sc.holes_plate_T("low"), np.array([[-10.0, 20.0, 3.0]])))[0, 0] == pytest.approx(0.0, abs=1e-9)
    assert sc.holes_plate_sdf("low", G.transform_points(sc.holes_plate_T("low"), np.array([[-45.0, 0.0, 1.5]])))[0, 0] > 10.0
    h = sc.panel_hole_world(sc.scene_of(sc.SCENES["panel200-s0"]))
    assert h["diameter"] == pytest.approx(25.20, abs=0.01)  # the cut's edge as the Hole tool's circle sees it
    names = {s.name for s in sc.scenes_for("quick")}
    assert names == {"e2e-sphere", "plate05-s0"}
    assert len(sc.scenes_for("core")) > len(names) and all(s.thin for s in sc.scenes_for("core", ["fins", "sheet"]))


TINY = sc.SceneSpec("tiny-s0", "tiny", "sphere", 0, 4, {"maker": "scan", "scene": "sphere_on_plane", "fps": 30.0})
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def _fake_frames(path: Path, n: int, cut_last: bool = False) -> None:
    """A recording's frames as the renderer leaves them: frames.csv and one PNG a row (the last one
    cut off mid-write when ``cut_last``)."""
    (path / "frames").mkdir(parents=True, exist_ok=True)
    rows = ["index,t_host,file"]
    for i in range(n):
        body = b"\x89PNG\r\n\x1a\n" + b"x" * 64
        (path / "frames" / f"{i:06d}.png").write_bytes(body if cut_last and i == n - 1 else body + PNG_END)
        rows.append(f"{i},{100 + i / 30:.6f},{i:06d}.png")
    (path / "frames.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_a_half_rendered_recording_is_rendered_again(tmp_path, monkeypatch):
    """A render killed part way (no marker, too few frames, an old empty marker over too few frames, or
    the last PNG cut off) is never used: it is rendered again; a complete one is never rendered twice."""
    import ohs.sim.datasets as DS

    calls = []

    def fake_make(out_dir, scene=None, n_frames=0, **kw):
        calls.append(n_frames)
        _fake_frames(Path(out_dir), n_frames)

    monkeypatch.setattr(DS, "make_scan_recording", fake_make)
    path = sc.rec_path(tmp_path, TINY)
    _fake_frames(path, 2)  # killed after 2 of 4 frames
    assert not sc.recording_complete(path, TINY)
    (path / "done").write_text("", encoding="utf-8")  # a marker of the old kind, over a cut-off recording
    assert not sc.recording_complete(path, TINY)
    _fake_frames(path, 4, cut_last=True)
    assert not sc.recording_complete(path, TINY)  # every row there, the last PNG cut off
    sc.render(tmp_path, TINY, say=lambda m: None)
    assert calls == [4] and sc.recording_complete(path, TINY)
    sc.render(tmp_path, TINY, say=lambda m: None)
    assert calls == [4]  # complete: kept
    with pytest.raises(RuntimeError):  # a job never runs on a cut-off recording
        _fake_frames(path, 3)
        sc.run_job(tmp_path, TINY, "tracked", "gpu", {}, tmp_path / "out")


def test_a_result_is_used_only_when_its_run_finished(tmp_path):
    p = tmp_path / "r" / "result.json"
    p.parent.mkdir()
    p.write_text('{"metrics": {"part_med": 0.1', encoding="utf-8")  # cut off mid-write
    assert sc.read_result(p) is None
    p.write_text('{"info": {}}', encoding="utf-8")  # not a finished run's result
    assert sc.read_result(p) is None
    sc.write_json(p, {"metrics": {"part_med": 0.1}, "info": {}})
    assert sc.read_result(p)["metrics"]["part_med"] == 0.1
    assert [f.name for f in p.parent.iterdir()] == ["result.json"]  # written whole, then moved into place


def test_the_baseline_carries_on_after_a_kill(tmp_path, monkeypatch):
    """A run killed after its first job, then started again with the same command: the finished job is
    used again, the others run, and the half-done one (a job killed before it wrote its result) is run
    again; nothing is run twice that finished."""
    import ohs.sim.datasets as DS

    monkeypatch.setattr(DS, "make_scan_recording", lambda out_dir, n_frames=0, **kw: _fake_frames(Path(out_dir), n_frames))
    specs = [TINY, dataclasses_replace(TINY, name="tiny-s1", seed=1)]
    monkeypatch.setitem(sc.SCENES, "tiny-s1", specs[1])
    started = []

    class FakeJob:
        kill_after = 1

        def __init__(self, cmd, **kw):
            out = Path(cmd[cmd.index("--out") + 1])
            started.append((cmd[cmd.index("--rec") + 1], cmd[cmd.index("--poses") + 1]))
            if len(started) > FakeJob.kill_after:
                (out / "proc").mkdir(parents=True, exist_ok=True)  # the job had begun, then everything was killed
                (out / "result.json").write_text('{"metrics": {"part_m', encoding="utf-8")  # mid-write
                raise KeyboardInterrupt
            sc.write_json(out / "result.json", {"metrics": {"part_med": 0.1}, "info": {}})
            self.returncode, self.pid = 0, 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(sc.subprocess, "Popen", FakeJob)
    with pytest.raises(KeyboardInterrupt):
        sc.run_set(tmp_path, specs, "gpu", {})
    assert started == [("tiny-s0", "tracked"), ("tiny-s0", "true")]
    FakeJob.kill_after = 99
    started.clear()
    res = sc.run_set(tmp_path, specs, "gpu", {})
    assert started == [("tiny-s0", "true"), ("tiny-s1", "tracked"), ("tiny-s1", "true")]
    assert all(res[s.name][p]["metrics"]["part_med"] == 0.1 for s in specs for p in ("tracked", "true"))
    started.clear()
    sc.run_set(tmp_path, specs, "gpu", {})
    assert started == []  # all finished: nothing is run again


def test_the_app_chain_carries_on_after_a_kill(tmp_path, monkeypatch):
    """The app chain's runs (``--chain app``) are restartable as the frame pass's: after a kill (the PC shut down) a
    job with a ``running`` marker of a process that is gone and a half-written result is run again, finished ones are
    used again, and a frame pass's result of the same recording is never taken for the app chain's. The child is told
    the chain, and a true-pose run is shared by settings of Straighten alone."""
    import ohs.sim.datasets as DS

    monkeypatch.setattr(DS, "make_scan_recording", lambda out_dir, n_frames=0, **kw: _fake_frames(Path(out_dir), n_frames))
    started = []

    class FakeJob:
        def __init__(self, cmd, **kw):
            out = Path(cmd[cmd.index("--out") + 1])
            chain = cmd[cmd.index("--chain") + 1] if "--chain" in cmd else "frame"
            started.append((cmd[cmd.index("--poses") + 1], chain, out.parent.parent.name))
            sc.write_json(out / "result.json", {"metrics": {"part_med": 0.2 if chain == "app" else 0.1}, "info": {}})
            self.returncode, self.pid = 0, 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(sc.subprocess, "Popen", FakeJob)
    sc.run_set(tmp_path, [TINY], "gpu", {})  # the frame pass first: its folders must not answer for the app chain
    assert started == [("tracked", "frame", "default"), ("true", "frame", "default")]
    started.clear()
    # the app chain was killed with the PC: one job finished, the other left its marker (a pid that is gone) and half
    # a result
    done = sc.run_dir(tmp_path, "gpu", {}, TINY, "tracked", "app")
    done.mkdir(parents=True)
    sc.write_json(done / "result.json", {"metrics": {"part_med": 0.3}, "info": {}})
    cut = sc.run_dir(tmp_path, "gpu", {}, TINY, "true", "app")
    (cut / "proc" / "stages" / "track").mkdir(parents=True)
    (cut / "running").write_text("999999999", encoding="utf-8")
    (cut / "result.json").write_text('{"metrics": {"part_m', encoding="utf-8")
    res = sc.run_set(tmp_path, [TINY], "gpu", {}, chain="app")
    assert started == [("true", "app", "app-default")]
    assert res[TINY.name]["tracked"]["metrics"]["part_med"] == 0.3 and res[TINY.name]["true"]["metrics"]["part_med"] == 0.2
    assert not (cut / "running").exists()
    started.clear()
    sc.run_set(tmp_path, [TINY], "gpu", {}, chain="app")
    assert started == []  # all finished: nothing is run again
    # a setting of Straighten: a tracked run of its own, the true-pose run shared
    res = sc.run_set(tmp_path, [TINY], "gpu", {"curve.enabled": False}, chain="app")
    assert [s[:2] for s in started] == [("tracked", "app")] and started[0][2].startswith("app-p-")
    assert res[TINY.name]["true"]["metrics"]["part_med"] == 0.2


def dataclasses_replace(spec, **kw):
    import dataclasses

    return dataclasses.replace(spec, **kw)


def _res(vals: dict[str, dict[str, float | None]]) -> dict:
    return {rec: {"tracked": {"metrics": dict(m)}} for rec, m in vals.items()}


def test_the_verdict_per_scene_with_made_up_results():
    """Made-up results, no rendering: a scene better on every seed, one the same within its spread, one
    with a single recording (no spread), one where one seed alone got worse by more than the spread, a
    thin wall one recording thicker and a small hole further off on one seed: each scene's verdict, and
    the last line lists exactly the scenes that are worse."""
    br = [f"bracket240-s{k}" for k in range(3)]
    fins = [f"fins-s{k}" for k in range(3)]
    hl = [f"holes-low-s{k}" for k in range(3)]
    pl = [f"panel200-s{k}" for k in range(3)]
    specs = [sc.SCENES[n] for n in br + fins + hl + pl + ["e2e-sphere"]]
    base = _res({**{n: {"part_med": 0.10 + 0.02 * k, "br_boss_left_r_err": -0.15} for k, n in enumerate(br)},
                 **{n: {"part_med": 0.05 + 0.01 * k, "wall1_err": 0.70 + 0.1 * k} for k, n in enumerate(fins)},
                 **{n: {"part_med": 0.06 + 0.01 * k, "h10_d_err": -0.30 + 0.1 * k} for k, n in enumerate(hl)},
                 **{n: {"part_med": 0.04 + 0.01 * k} for k, n in enumerate(pl)},
                 "e2e-sphere": {"part_med": 0.06}})
    new = _res({**{n: {"part_med": 0.05 + 0.02 * k, "br_boss_left_r_err": -0.14} for k, n in enumerate(br)},  # better
                **{n: {"part_med": 0.05 + 0.01 * k, "wall1_err": 0.70 + 0.1 * k + (0.08 if k == 1 else 0.0)}
                   for k, n in enumerate(fins)},  # one wall 0.08 mm thicker: within the spread (0.2), still an alarm
                **{n: {"part_med": 0.06 + 0.01 * k, "h10_d_err": (-0.30 + 0.1 * k) - (0.12 if k == 2 else 0.0)}
                   for k, n in enumerate(hl)},  # the Ø10 hole 0.12 mm further off on one seed: a small feature
                **{n: {"part_med": 0.04 + 0.01 * k + (0.05 if k == 0 else -0.001)} for k, n in enumerate(pl)},  # one seed worse by more than the spread
                "e2e-sphere": {"part_med": 0.05}})
    rows = sc.compare_rows(base, new, specs)
    per = {(s["scene"], s["poses"]): s for s in sc.scene_verdicts(rows)}
    assert per[("bracket240", "tracked")]["verdict"] == "better"
    assert per[("e2e-sphere", "tracked")]["verdict"] == sc.NO_SPREAD
    assert per[("fins", "tracked")]["verdict"] == "worse" and "wall1_err" in per[("fins", "tracked")]["why"]
    assert per[("holes-low", "tracked")]["verdict"] == "worse" and "h10_d_err" in per[("holes-low", "tracked")]["why"]
    assert per[("panel200", "tracked")]["verdict"] == "worse" and "panel200-s0" in per[("panel200", "tracked")]["why"]
    text = "\n".join(sc.compare_table(rows))
    assert text.index("THIN PARTS AND SMALL FEATURES") < text.index("## Per scene") < text.index("## Summary")
    assert "| holes-low | tracked | h10_d_err |" in text.split("## Worse")[0]  # a small feature is shown with the thin parts
    last = text.rstrip().splitlines()[-1]
    assert last.startswith("**Worse on: ") and "fins (tracked" in last and "holes-low (tracked" in last
    assert "panel200 (tracked" in last and "bracket240" not in last and "e2e-sphere" not in last
    good = _res({n: base[n]["tracked"]["metrics"] for n in br + ["e2e-sphere"]})
    rows = sc.compare_rows(good, good, [sc.SCENES[n] for n in br + ["e2e-sphere"]])
    assert {s["verdict"] for s in sc.scene_verdicts(rows)} == {"identical"}
    assert "\n".join(sc.compare_table(rows)).rstrip().splitlines()[-1] == "**Better or the same on every scene.**"


def test_the_chain_is_part_of_the_key(tmp_path):
    """ENGINE_PLAN X0a: the frame pass keeps the folders it always had (so every saved run and baseline is found
    again); the app chain has its own; Straighten's settings are left out of a true-pose run's key (it is not
    straightened), but not out of a tracked one's."""
    p = {"fusion.voxel_mm": 0.5}
    c = {"curve.holdout_max_ratio": 0.5}
    assert sc.settings_key({}) == sc.settings_key({}, "frame", "tracked") == "default"
    assert sc.settings_key(p) == sc.settings_key(p, "frame", "true") and sc.settings_key(p).startswith("p-")
    assert sc.settings_key({}, "app", "tracked") == sc.settings_key({}, "app", "true") == "app-default"
    assert sc.settings_key(p, "app") == "app-" + sc.settings_key(p)
    assert sc.settings_key(c, "app", "true") == "app-default" != sc.settings_key(c, "app", "tracked")
    assert sc.settings_key({**p, **c}, "app", "true") == sc.settings_key(p, "app", "tracked")
    spec = sc.SCENES["fins-s0"]
    assert sc.run_dir(Path("C"), "gpu", {}, spec, "tracked") == Path("C") / "runs" / "gpu" / "default" / "fins-s0" / "tracked"
    assert sc.run_dir(Path("C"), "gpu", {}, spec, "true", "app") == Path("C") / "runs" / "gpu" / "app-default" / "fins-s0" / "true"
    assert sc.parse_params(["curve.holdout_max_ratio=0.5"]) == c
    assert sc.curve_params({}) is None and sc.curve_params(c).holdout_max_ratio == 0.5
    with pytest.raises(ValueError):
        sc.curve_params({"curve.no_such_field": 1})
    with pytest.raises(SystemExit):  # a setting of Straighten means nothing to the frame pass
        sc.main(["run", "--set", "quick", "--cache", str(tmp_path), "--set-param", "curve.holdout_max_ratio=0.5"])


def test_the_app_chains_parameters_are_the_servers(tmp_path):
    """The app chain fuses with the server's own stage parameters (every piece kept, PLY only), not the frame pass's
    (which cleans small pieces before the recipe)."""
    import dataclasses

    from ohs.app.config import Config
    from ohs.server.processing import stage_params

    sys.path.insert(0, str(SOFTWARE / "tests"))
    from recordings import scan_recording

    rec = scan_recording(tmp_path / "rec", scene="sphere_on_plane", n_frames=3, image_size=(640, 400), realistic=True,
                         depth_every=0, seed=2)
    spec = sc.SCENES["e2e-sphere"]
    app = sc.app_params(spec, {}, rec)
    assert dataclasses.asdict(app) == dataclasses.asdict(stage_params(Config(), "markerless", "standard", 640))
    assert app.clean_min_component == 0.0 and app.export == ("ply",)
    assert sc.base_params(spec).clean_min_component > 0  # the frame pass, as it always ran
    assert sc.app_params(spec, {"fusion.voxel_mm": 0.75}, rec).pipeline.fusion.voxel_mm == 0.75


@pytest.mark.slow
def test_the_app_chain_gives_the_meshes_of_a_make_mesh(tmp_path, monkeypatch):
    """A real Make mesh through the server (Standard, the Default recipe) and the scorecard's app chain on the same
    recording give every stage's mesh byte for byte, and take the same decisions. So that every stage acts on this
    short scan, both get the same stand-ins: laser crossings that move every pose by 0.3 mm, and a Refine with lasers
    that changes the mesh at any share."""
    pytest.importorskip("fastapi")
    pytest.importorskip("open3d")
    requests = pytest.importorskip("requests")
    import time as _time

    import ohs.server.processing as SP
    import ohs.track.curve_ba as CB
    from ohs.app.config import Config
    from ohs.io.recording import RecordingReader
    from ohs.server.app import create_app
    from ohs.server.testing import ServerThread

    sys.path.insert(0, str(SOFTWARE / "tests"))
    from recordings import scan_recording

    rec = scan_recording(tmp_path / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    calls: list[tuple] = []

    def shifted(rec_path, calib, trajectory, *, reference=None, params=None, progress=None, cancelled=None):
        calls.append((len(trajectory), None if reference is None else len(reference[0]), params))
        poses = {int(i): np.asarray(T, float) @ G.make_se3(np.eye(3), [0.3, 0.0, 0.0]) for i, _, ok, T in trajectory if ok}
        return poses, {"changed": True, "reason": "adjusted", "crossings": 999, "frames": len(poses),
                       "holdout": {"ratio": 0.5}, "miss_mm": {"before": 0.2, "after": 0.1}, "moved_mm": 0.3,
                       "moved_deg": 0.0, "verdict": "moved"}

    monkeypatch.setattr(CB, "laser_curve_adjustment", shifted)
    monkeypatch.setattr(SP, "REFINE_MIN_SHARE", 0.0)
    cfg = Config()
    cfg.paths.calibration = str(tmp_path / "no-calibration.json")
    app = create_app(cfg, projects_dir=tmp_path / "projects", demo_dir=tmp_path / "demo", static_dir=tmp_path / "static",
                     data_dir=tmp_path / "data", config_path=tmp_path / "config.toml")
    with ServerThread(app) as srv:
        st = app.state.studio
        pid = requests.post(srv.url + "/api/projects", json={"name": "Chain"}, timeout=30).json()["id"]
        scan, rec_dir = st.projects.add_scan(pid, "Sphere", "markerless")
        shutil.copytree(rec, rec_dir)
        n = len(RecordingReader(rec_dir))
        st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=n, duration_s=n / 30.0)
        jid = requests.post(f"{srv.url}/api/projects/{pid}/scans/{scan['id']}/process", json={"quality": "standard"},
                            timeout=30).json()["id"]
        end = _time.monotonic() + 600.0
        while (j := requests.get(f"{srv.url}/api/jobs/{jid}", timeout=30).json())["state"] in ("queued", "running"):
            assert _time.monotonic() < end, "Make mesh did not finish within 10 minutes"
            _time.sleep(0.2)
        assert j["state"] == "done", j
        server = st.projects.scan_dir(pid, scan["id"]) / "process"
        state = json_load(server / "state.json")
    assert state["base"]["stage"] == "refine" and state["stages"]["straighten"]["state"] == "done"
    proc = tmp_path / "chain"
    rep, info = sc.app_chain(rec, proc, sc.SCENES["e2e-sphere"], {}, None)
    assert info["base"] == "refine" and info["straighten"]["adjusted"] and info["refine"]["acted"]
    assert info["straighten"]["looped"] is False and info["straighten"]["loops"] == state["stages"]["straighten"]["loops"]
    assert len(calls) == 2 and calls[0] == calls[1]  # the crossings were called the same way (rows, reference, params)
    for stage in ("track", "straighten", "refine"):
        assert same_mesh(server / stage / "mesh.ply", proc / "stages" / stage / "mesh.ply"), f"{stage}: the meshes differ"
        for name in ("trajectory.csv", "laser_points.ply"):
            assert (server / stage / name).read_bytes() == (proc / "stages" / stage / name).read_bytes(), (stage, name)
    assert (proc / "mesh.ply").read_bytes() == (proc / "stages" / "refine" / "mesh.ply").read_bytes()  # what the judge scores
    assert rep["refine"]["source"] == "straighten"


def same_mesh(a: Path, b: Path) -> bool:
    """The same mesh, every vertex, face, sigma and count of looks bit for bit, up to the numbering of the vertices
    (the graphics card's surface extraction numbers them in the order its threads finish: two runs of the same fuse
    write the same mesh in another order; the scorecard scores it renumbered by position)."""
    from ohs.mesh.order import canonical
    from ohs.track.export import load_ply

    A, B = load_ply(a), load_ply(b)
    ca = canonical(np.asarray(A["vertices"], np.float64), np.asarray(A["faces"], np.int64))
    cb = canonical(np.asarray(B["vertices"], np.float64), np.asarray(B["faces"], np.int64))
    if not (np.array_equal(ca.V, cb.V) and np.array_equal(ca.F, cb.F)):
        return False
    return all((A.get(k) is None and B.get(k) is None) or np.array_equal(ca.vertex_values(A.get(k)), cb.vertex_values(B.get(k)))
               for k in ("quality", "looks"))


def test_the_true_pose_app_run_is_not_straightened(tmp_path, monkeypatch):
    """At true poses the app chain fuses at them and refines; Straighten is not called."""
    import ohs.track.posegraph as PG

    sys.path.insert(0, str(SOFTWARE / "tests"))
    from ohs.io.recording import RecordingReader
    from recordings import scan_recording

    rec = scan_recording(tmp_path / "rec", scene="sphere_on_plane", n_frames=3, image_size=(640, 400), realistic=True,
                         depth_every=0, seed=2)

    def boom(*a, **k):
        raise AssertionError("Straighten ran at true poses")

    monkeypatch.setattr(PG, "straighten_trajectory", boom)
    _, info = sc.app_chain(rec, tmp_path / "p", sc.SCENES["e2e-sphere"], {}, RecordingReader(rec).ground_truth_poses())
    assert info["straighten"]["ran"] is False and info["base"] in ("track", "refine")
    assert "Straighten not run" in sc.stages_text(info)
    assert (tmp_path / "p" / "mesh.ply").read_bytes() == (tmp_path / "p" / "stages" / info["base"] / "mesh.ply").read_bytes()


def test_an_app_baseline_carries_what_the_stages_did(tmp_path):
    spec = sc.SCENES["fins-s0"]
    ch = {"chain": "app", "base": "straighten", "refine": {"acted": False, "moved_pct": 0.5},
          "straighten": {"ran": True, "loops": 0, "looped": False, "adjusted": True,
                         "crossings": {"crossings": 535, "holdout": {"ratio": 0.837}, "reason": "adjusted", "moved_mm": 1.2}}}
    res = {"fins-s0": {"tracked": {"metrics": {"part_med": 0.1}, "info": {"chain": ch}}}}
    p = sc.save_baseline(tmp_path, "b", "gpu", {}, res, [spec], "app")
    body = json_load(p)
    assert body["chain"] == "app" and body["results"]["fins-s0"]["tracked"]["stages"] == ch
    q = sc.save_baseline(tmp_path, "f", "gpu", {}, res, [spec])  # the frame pass: the form it always had
    assert "chain" not in json_load(q) and "stages" not in json_load(q)["results"]["fins-s0"]["tracked"]
    with pytest.raises(SystemExit):  # never overwritten
        sc.save_baseline(tmp_path, "b", "gpu", {}, res, [spec], "app")
    text = "\n".join(sc.stages_table(res, [spec]))
    assert "| fins-s0 | tracked | 0 (no) | 535 | 0.837 | yes (adjusted) | 1.20 | no (0.5) | straighten |" in text


def json_load(p: Path) -> dict:
    import json

    return json.loads(Path(p).read_text(encoding="utf-8"))


def test_paired_replay_on_a_tiny_cached_depth(tmp_path):
    """The matcher's depth of 3 frames captured once, then fed back: the same mesh vertex for vertex;
    a fusing setting then changes the mesh while the depth stays the cached one."""
    sys.path.insert(0, str(SOFTWARE / "tests"))
    from recordings import scan_recording

    from ohs.app.process import ProcessParams, process_recording
    from ohs.io.recording import RecordingReader
    from ohs.track.export import load_ply
    from tools.snapshot import canonical_mesh

    work = tmp_path / "replay"
    try:
        rec = scan_recording(work / "rec", scene="sphere_on_plane", n_frames=3, image_size=(640, 400), realistic=True,
                             depth_every=0, seed=2)
        gt = RecordingReader(rec).ground_truth_poses()
        pp = ProcessParams(export=("ply",), health=False)

        def fuse(mode, out, params=pp):
            restore = sc._install_depth_hook(mode, work / "depth")
            try:
                process_recording(rec, out, params=params, poses=gt)
            finally:
                restore()
            d = load_ply(out / "mesh.ply")
            return canonical_mesh(d["vertices"], d["faces"])

        Va, Fa = fuse("capture", work / "a")
        assert len(list((work / "depth").glob("*.npz"))) == 3
        Vb, Fb = fuse("feed", work / "b")
        assert np.array_equal(Va, Vb) and np.array_equal(Fa, Fb)
        import dataclasses

        coarse = dataclasses.replace(pp, pipeline=sc.apply_params(pp, {"fusion.voxel_mm": 1.5}).pipeline)
        Vc, _ = fuse("feed", work / "c", coarse)
        assert len(Vc) != len(Va)  # the setting acts on the replay
    finally:
        shutil.rmtree(work, ignore_errors=True)
