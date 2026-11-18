"""Calibration health, part 1 (FEATURE_PLAN H1): what each scan writes down of its calibration.

* The calibration file's optional keys (``id``, ``created``, ``temperature_c``, ``steps``): an old
  file without them loads and saves as before; a new one keeps them through a load and a save;
  ``merge_calibration`` stamps them, per step.
* The laser points' signed distance to the stereo depth, measured before the depth check drops
  any (``laser_points(depth_gaps=...)``): it changes no point, and it sees a shifted laser plane.
* ``report["health"]`` of ``process_recording``: every key there, and the mesh, the poses and the
  laser points bit-identical with and without it. Run on the GPU path and with ``OHS_COMPUTE=cpu``.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs.app import capture  # noqa: E402
from ohs.app.pipeline import FramePipeline, PipelineParams  # noqa: E402
from ohs.app.process import ProcessParams, process_recording  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.sim.nominal import nominal_calibration  # noqa: E402
from ohs.types import LaserPlane, ScannerCalibration  # noqa: E402
from ohs.vision.laser import laser_points  # noqa: E402
from recordings import scan_recording  # noqa: E402

N = 8


@pytest.fixture(scope="module")
def rec(tmp_path_factory) -> Path:
    # the recording of test_app_pipeline.py (the same arguments: one render, shared by the cache)
    return scan_recording(tmp_path_factory.mktemp("health") / "rec", n_frames=N, image_size=(640, 400),
                          realistic=True, seed=4, depth_every=0)


# --------------------------------------------------------------------------- the calibration file
def _old_text(calib: ScannerCalibration) -> str:
    """A calibration file as every version before H1 wrote it."""
    d = calib.to_dict()
    for k in ("id", "created", "temperature_c", "steps"):
        d.pop(k, None)
    return json.dumps(d, indent=2)


def test_an_old_calibration_file_loads_and_saves_as_before(tmp_path):
    old = _old_text(nominal_calibration((640, 400), realistic=True))
    src = tmp_path / "old.json"
    src.write_text(old, encoding="utf-8")
    c = ScannerCalibration.load(src)
    assert c.id is None and c.created is None and c.temperature_c is None and c.steps is None
    c.save(tmp_path / "again.json")
    assert (tmp_path / "again.json").read_text(encoding="utf-8") == old


def test_a_new_calibration_file_keeps_its_keys(tmp_path):
    c = nominal_calibration((640, 400), realistic=True)
    c = dataclasses.replace(c, id="a1b2c3d4e5f6", created="2026-11-18T09:30:00+00:00", temperature_c=31.25,
                            steps={"stereo": {"created": "2026-11-18T09:00:00+00:00", "temperature_c": 30.5},
                                   "lasers": {"created": "2026-11-18T09:30:00+00:00", "temperature_c": 31.25}})
    c.save(tmp_path / "new.json")
    d = json.loads((tmp_path / "new.json").read_text(encoding="utf-8"))
    assert d["id"] == "a1b2c3d4e5f6" and d["created"] == "2026-11-18T09:30:00+00:00" and d["temperature_c"] == 31.25
    back = ScannerCalibration.load(tmp_path / "new.json")
    assert (back.id, back.created, back.temperature_c, back.steps) == (c.id, c.created, c.temperature_c, c.steps)
    back.save(tmp_path / "again.json")
    assert (tmp_path / "again.json").read_text(encoding="utf-8") == (tmp_path / "new.json").read_text(encoding="utf-8")
    # a recording carries a copy of its calibration, keys and all
    from ohs.io.recording import RecordingWriter

    with RecordingWriter(tmp_path / "rec") as w:
        w.write_calibration(c)
    got = RecordingReader(tmp_path / "rec").calibration()
    assert got.id == c.id and got.steps == c.steps


def test_merge_calibration_stamps_each_step():
    full = nominal_calibration((640, 400), realistic=True)
    stereo_only = ScannerCalibration(full.stereo)
    s1, _ = capture.merge_calibration(None, "stereo", full.stereo, temperature_c=25.0, when="2026-11-18T08:00:00+00:00")
    assert s1.created == "2026-11-18T08:00:00+00:00" and s1.temperature_c == 25.0 and s1.id
    assert s1.steps == {"stereo": {"created": "2026-11-18T08:00:00+00:00", "temperature_c": 25.0}}
    s2, _ = capture.merge_calibration(s1, "lasers", dict(full.lasers), temperature_c=27.5,
                                      when="2026-11-18T08:10:00+00:00")
    assert s2.id != s1.id and s2.created == "2026-11-18T08:10:00+00:00" and s2.temperature_c == 27.5
    assert s2.steps == {"stereo": s1.steps["stereo"], "lasers": {"created": "2026-11-18T08:10:00+00:00",
                                                                 "temperature_c": 27.5}}
    s3, _ = capture.merge_calibration(s2, "imu", full.imu, when="2026-11-18T08:20:00+00:00")
    assert s3.id not in (s1.id, s2.id) and s3.temperature_c is None
    assert set(s3.steps) == {"stereo", "lasers", "imu"} and s3.steps["lasers"] == s2.steps["lasers"]
    # redoing stereo drops the lasers and the IMU, and their steps with them
    s4, _ = capture.merge_calibration(s3, "stereo", full.stereo, temperature_c=40.0, when="2026-11-18T09:00:00+00:00")
    assert not s4.lasers and s4.imu is None and set(s4.steps) == {"stereo"} and s4.temperature_c == 40.0
    # ... unless they are kept
    s5, _ = capture.merge_calibration(s3, "stereo", full.stereo, keep=True, when="2026-11-18T09:00:00+00:00")
    assert set(s5.steps) == {"stereo", "lasers", "imu"} and s5.steps["imu"] == s3.steps["imu"]
    # an old calibration (no keys) gets them on its next step; the steps it had before are unknown
    s6, _ = capture.merge_calibration(stereo_only, "imu", full.imu, temperature_c=30.0,
                                      when="2026-11-18T10:00:00+00:00")
    assert s6.id and set(s6.steps) == {"imu"}
    # without a time the step is stamped now
    s7, _ = capture.merge_calibration(None, "stereo", full.stereo)
    assert s7.created and s7.created.endswith("+00:00") and s7.steps["stereo"]["temperature_c"] is None


def test_recording_temperature_reads_the_meta(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"temperature_c": {"first": 30.0, "last": 32.0, "min": 30.0,
                                                                       "max": 32.5, "n": 9}}), encoding="utf-8")
    assert capture.recording_temperature(tmp_path) == 31.0
    (tmp_path / "meta.json").write_text("{}", encoding="utf-8")
    assert capture.recording_temperature(tmp_path) is None
    assert capture.recording_temperature(tmp_path / "missing") is None


# --------------------------------------------------------------------------- laser points against the stereo depth
def _gaps(rec: Path, calib: ScannerCalibration, scale: float = 0.5) -> tuple[dict, dict, list]:
    """``(gaps per line, rejected per line, [(frame result, points without gaps)])`` over the recording."""
    reader = RecordingReader(rec)
    gaps: dict[str, list[np.ndarray]] = {}
    rejected: dict[str, int] = {}
    pairs = []
    with FramePipeline(calib, PipelineParams(scale=scale)) as pipe:
        pipe.add_imu(reader.imu_samples())
        for i in range(N):
            r = pipe.process(reader.frame(i))
            plain = laser_points(r.left_r, r.right_r, pipe.K, pipe.baseline, pipe.planes, pipe.laser_params,
                                 lines_left=r.lines, lines_right=r.lines_right, depth=r.depth, depth_sigma=r.sigma)
            got = laser_points(r.left_r, r.right_r, pipe.K, pipe.baseline, pipe.planes, pipe.laser_params,
                               lines_left=r.lines, lines_right=r.lines_right, depth=r.depth, depth_sigma=r.sigma,
                               rejected=rejected, depth_gaps=gaps)
            pairs.append((plain, got))
    return {k: np.concatenate(v) for k, v in gaps.items()}, rejected, pairs


def test_laser_depth_gaps_change_no_point_and_see_a_shifted_plane(rec):
    calib = RecordingReader(rec).calibration()
    gaps, rejected, pairs = _gaps(rec, calib)
    for plain, got in pairs:  # measuring changes nothing
        assert list(plain) == list(got)
        assert all(plain[k].tobytes() == got[k].tobytes() for k in plain)
    assert set(gaps) == {"vertical", "horizontal"}
    for name, g in gaps.items():
        ok = g[np.isfinite(g)]
        # every point the depth check looked at, the dropped ones too
        assert len(g) >= rejected.get(name, 0) and len(ok) > 200, name
        assert abs(float(np.median(ok))) < 0.3, (name, float(np.median(ok)))
    # the vertical plane 1.5 mm off along its normal: its points move away from the stereo surface, the
    # horizontal line's stay
    planes = dict(calib.lasers)
    v = planes["vertical"]
    planes["vertical"] = LaserPlane(v.name, v.normal, v.d - 1.5, v.rms, v.n_points)
    moved, _, _ = _gaps(rec, dataclasses.replace(calib, lasers=planes))
    med = {k: float(np.nanmedian(g)) for k, g in moved.items()}
    assert abs(med["vertical"]) > 0.8, med
    assert abs(med["horizontal"] - float(np.nanmedian(gaps["horizontal"]))) < 0.05, med


# --------------------------------------------------------------------------- the health block
HEALTH_KEYS = {"version", "calibration", "temperature_c", "row_error", "laser_depth", "laser_mesh", "tracking",
               "straighten", "ms"}


def test_the_health_block_has_every_key(rec, tmp_path):
    calib = RecordingReader(rec).calibration()
    calib = dataclasses.replace(calib, id="0123456789ab", created="2026-11-18T08:00:00+00:00", temperature_c=24.0,
                                steps={"stereo": {"created": "2026-11-18T08:00:00+00:00", "temperature_c": 24.0}})
    # full resolution: the laser cloud against the mesh needs good poses, and the CPU path's tracking
    # drifts tens of mm on this recording at half resolution (and differently in every run)
    p = ProcessParams(export=("ply",), row_error_every=3)
    rep = process_recording(rec, tmp_path / "out", calib=calib, params=p)
    assert json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8")) == rep
    from ohs.calib import scan_health

    h = rep["health"]
    assert set(h) == HEALTH_KEYS and h["version"] == scan_health.HEALTH_VERSION
    assert h["calibration"] == {"id": "0123456789ab", "created": "2026-11-18T08:00:00+00:00", "temperature_c": 24.0,
                                "steps": {"stereo": {"created": "2026-11-18T08:00:00+00:00", "temperature_c": 24.0}},
                                "source": "argument"}
    # the simulator's IMU warms up during the scan (ohs.sim.datasets)
    t = h["temperature_c"]
    assert set(t) == {"scan", "calibration", "change"} and t["calibration"] == 24.0
    assert set(t["scan"]) == {"first", "last", "min", "max", "n"} and t["scan"]["n"] >= 1
    assert t["change"] == {"min": pytest.approx(t["scan"]["min"] - 24.0), "max": pytest.approx(t["scan"]["max"] - 24.0)}
    # the row error on frames 0, 3 and 6, in full-resolution pixels: a true calibration has none to speak of
    r = h["row_error"]
    assert set(r) == {"every", "frames", "matches", "median_px", "p90_px", "signed_px", "per_frame"}
    assert r["every"] == 3 and r["frames"] == 3 and [f["index"] for f in r["per_frame"]] == [0, 3, 6]
    assert all(set(f) == {"index", "matches", "median_px", "signed_px"} for f in r["per_frame"])
    assert r["matches"] == sum(f["matches"] for f in r["per_frame"]) and r["matches"] > 100
    assert r["median_px"] < 0.3 and abs(r["signed_px"]) < 0.3 and r["p90_px"] >= r["median_px"]
    # the laser lines against the stereo depth (before the drop) and against the mesh
    ld = h["laser_depth"]
    assert set(ld) == {"sign", "per_line"} and set(ld["per_line"]) == {"vertical", "horizontal"}
    for name, s in ld["per_line"].items():
        assert set(s) == {"frames", "points", "with_depth", "median_mm", "abs_median_mm", "p05_mm", "p95_mm",
                          "rejected"}, s
        assert s["frames"] == N and s["with_depth"] > 100 and s["points"] >= s["with_depth"]
        assert s["rejected"] == rep["lasers"]["n_rejected_by_depth"].get(name, 0)
        assert abs(s["median_mm"]) < 0.3 and s["p05_mm"] <= s["median_mm"] <= s["p95_mm"]
    lm = h["laser_mesh"]
    assert set(lm) == {"all", "per_line"} and set(lm["per_line"]) == {"vertical", "horizontal"}
    assert {"gate_mm", "points", "stats"} <= set(lm["all"]) and lm["all"]["points"]["used"] > 100
    for s in lm["per_line"].values():
        assert set(s) == {"points", "used", "stray", "median_mm", "mean_mm", "abs_p95_mm"}
        assert s["used"] > 50 and abs(s["median_mm"]) < 0.3
    # the tracker's residual and its rotation against the gyro's
    tr = h["tracking"]
    assert set(tr) == {"frames", "rmse_mm", "gyro_jump_deg"} and tr["frames"] == N
    assert tr["rmse_mm"]["n"] == N - 1 and 0 < tr["rmse_mm"]["median"] < 1.5  # the first frame has no ICP
    assert tr["gyro_jump_deg"]["n"] == N - 1 and tr["gyro_jump_deg"]["median"] < 0.2
    assert all("jump_deg" in row for row in rep["per_frame"][1:])  # also kept per frame
    assert h["straighten"] is None  # a server stage: process_recording does not straighten
    assert h["ms"] >= 0
    assert len(json.dumps(h)) < 8000


def _mesh_as_set(path: Path) -> bytes:
    """The mesh with its vertices (and their normals and sigmas) in sorted order and its faces
    renumbered and sorted: the GPU writes the same mesh in a different order from run to run."""
    from ohs.track.export import load_ply

    m = load_ply(path)
    V = m["vertices"]
    order = np.lexsort(V.T[::-1])
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    F = np.sort(rank[m["faces"]], axis=1)
    F = F[np.lexsort(F.T[::-1])]
    extra = [np.asarray(m[k])[order].tobytes() for k in ("normals", "quality") if m.get(k) is not None]
    return V[order].tobytes() + F.tobytes() + b"".join(extra)


def _outputs(out: Path) -> dict[str, bytes]:
    return {"trajectory.csv": (out / "trajectory.csv").read_bytes(),
            "laser_points.ply": (out / "laser_points.ply").read_bytes(), "mesh": _mesh_as_set(out / "mesh.ply")}


def test_the_health_block_changes_no_result(rec, tmp_path):
    """The mesh, the poses and the laser points are bit-identical with and without the block.

    Tracked: on the GPU path the poses and the laser points come out byte for byte the same from
    run to run, the mesh the same up to its order; on the CPU path Open3D's tracking is not
    repeatable (its poses differ from run to run in the 4th decimal at full resolution, and two
    runs agree only by chance), so a tracked pass is compared on the GPU path only. At given poses
    (Straighten, Fuse) both paths repeat exactly, and there the block changes nothing at all."""
    p = ProcessParams(pipeline=PipelineParams(scale=0.5), export=("ply",), row_error_every=2)
    off = dataclasses.replace(p, health=False)
    on_rep = process_recording(rec, tmp_path / "on", params=p)
    off_rep = process_recording(rec, tmp_path / "off", params=off)
    process_recording(rec, tmp_path / "off2", params=off)
    assert "health" in on_rep and "health" not in off_rep
    if on_rep["params"]["compute"]["tracking_model"] == "gpu":
        a, b, b2 = (_outputs(tmp_path / d) for d in ("on", "off", "off2"))
        assert b == b2  # the GPU path repeats itself: what follows is a real comparison
        assert a == b
        for k in ("frames", "failures", "imu", "lasers", "ground_truth"):
            assert on_rep[k] == off_rep[k], k
        for x, y in zip(on_rep["per_frame"], off_rep["per_frame"]):
            assert {k: v for k, v in x.items() if k != "total_ms"} == {k: v for k, v in y.items() if k != "total_ms"}
    # at given poses, on either path
    from ohs.track.posegraph import read_trajectory

    poses = {i: T for i, _, ok, T in read_trajectory(tmp_path / "off" / "trajectory.csv") if ok}
    process_recording(rec, tmp_path / "g_on", params=p, poses=poses)
    process_recording(rec, tmp_path / "g_off", params=off, poses=poses)
    assert _outputs(tmp_path / "g_on") == _outputs(tmp_path / "g_off")


def test_given_poses_write_the_block_too(rec, tmp_path):
    p = ProcessParams(pipeline=PipelineParams(scale=0.5), export=("ply",))
    first = process_recording(rec, tmp_path / "track", params=p)
    from ohs.track.posegraph import read_trajectory

    poses = {i: T for i, _, ok, T in read_trajectory(tmp_path / "track" / "trajectory.csv") if ok}
    rep = process_recording(rec, tmp_path / "fuse", params=p, poses=poses)
    h = rep["health"]
    assert set(h) == HEALTH_KEYS and h["calibration"]["source"] == "recording" and h["calibration"]["id"] is None
    assert h["row_error"]["frames"] == 1  # frame 0 of 8
    # nothing is tracked at given poses: no residual, no gyro
    assert h["tracking"] == {"frames": N, "rmse_mm": {"n": 0}, "gyro_jump_deg": {"n": 0}}
    assert first["health"]["tracking"]["rmse_mm"]["n"] == N - 1


# --------------------------------------------------------------------------- what Straighten corrected
def test_straighten_summary():
    from ohs.calib import scan_health

    rep = {"keyframes": 12, "loop_candidates": 3, "loops_accepted": 2, "correction_mm": 1.84, "correction_deg": 0.21,
           "loops": [[3, 40], [5, 52]], "verdict": "Closed 2 loops: the end moved 1.8 mm into place.", "changed": True,
           "reason": "straightened", "drift_scale": 1.0, "candidates": [{"frames": [3, 40]}] * 3,
           "residual_mm": {"before_max": 2.1, "after_max": 0.2, "before_rms": 1.5, "after_rms": 0.1,
                           "odometry_chi2_max": 3.0},
           "laser_curves": {"frames": 50, "crossings": 597, "miss_mm": {"before": 0.1, "after": 0.04},
                            "moved_mm": 1.1, "moved_deg": 0.05, "changed": True, "reason": "aligned",
                            "verdict": "x", "timings_s": {}}, "timings_s": {}, "params": {}}
    s = scan_health.straighten_summary(rep)
    assert s == {"reason": "straightened", "changed": True, "keyframes": 12, "loop_candidates": 3, "loops_accepted": 2,
                 "correction_mm": 1.84, "correction_deg": 0.21, "residual_mm": {"before_max": 2.1, "after_max": 0.2},
                 "drift_scale": 1.0,
                 "laser_curves": {"changed": True, "reason": "aligned", "crossings": 597,
                                  "miss_mm": {"before": 0.1, "after": 0.04}, "moved_mm": 1.1, "moved_deg": 0.05}}
    bare = scan_health.straighten_summary({"reason": "no_candidates", "changed": False})
    assert bare["reason"] == "no_candidates" and bare["laser_curves"] is None and bare["residual_mm"] is None
    assert scan_health.straighten_summary(None) is None
