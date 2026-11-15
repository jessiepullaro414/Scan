"""Laser-plane calibration for any set of line lasers: the 4-laser variant end to end.

Simulated 4-laser board views (``ohs.sim.datasets.make_laser_calib_set`` with a layout of
``ohs.sim.nominal.LASER_LAYOUTS``) run through ``ohs.app.calib_flow``. The calibration knows only
the laser names; it separates the two lines of a family and names them by where each sheet starts,
so the layouts here are only the truth the results are compared with. Run with ``-s`` to see the
``METRICS`` lines (plane errors per laser).
"""
from __future__ import annotations

import numpy as np
import pytest

from ohs import geometry as G
from ohs.app.calib_flow import check_from_recording, lasers_from_recording
from ohs.app.capture import LINE_MIN_POINTS, BoardProcessor, missing_lines
from ohs.calib.board import CharucoSpec
from ohs.sim.datasets import laser_calib_board_poses, make_laser_calib_set
from ohs.sim.nominal import LASER_SETS, nominal_calibration
from ohs.types import LaserPlane, ScannerCalibration, StereoFrame
from recordings import cached_result
from test_e2e_calibration import metrics, plane_errors, write_pairs

FOUR = LASER_SETS[4]
TWO = LASER_SETS[2]


def stereo_only(calib: ScannerCalibration) -> ScannerCalibration:
    return ScannerCalibration(calib.stereo, {}, None)


@pytest.fixture(scope="module", params=["4", "4-cross250", "4-cross350"])
def four_set(request, tmp_path_factory):
    layout = request.param
    pairs, calib, poses, spec = cached_result(make_laser_calib_set, n_views=8, realistic=False, seed=1, layout=layout)
    rec = write_pairs(tmp_path_factory.mktemp("la4") / "rec", pairs, purpose="laser_calibration")
    return layout, rec, calib, CharucoSpec(**spec), pairs


def test_four_lasers_are_found_and_named(four_set):
    layout, rec, true, spec, _ = four_set
    assert set(true.lasers) == set(FOUR)
    planes, rep = lasers_from_recording(rec, stereo_only(true), spec, names=FOUR)
    assert rep["ok"] and set(planes) == set(FOUR), {n: e.get("reason") for n, e in rep["lasers"].items()}
    assert rep["frames_with_board"] == 8 and rep["names"] == list(FOUR)
    err = plane_errors(planes, true.lasers)
    metrics(f"lasers4_{layout}", **{f"{n}_{k}": v for n, e in err.items() for k, v in e.items()},
            **{f"{n}_views": rep["lasers"][n]["n_observations"] for n in FOUR},
            **{f"{n}_points": rep["lasers"][n]["n_points"] for n in FOUR})
    for name in FOUR:
        e = err[name]
        # the tolerance of the two-laser test (test_e2e_calibration.test_lasers_margin_beats_board)
        assert e["angle_deg"] < 0.03 and abs(e["d_err_mm"]) < 0.1, (layout, name, e)
        assert e["point_err_median_mm"] < 0.05 and e["point_err_max_mm"] < 0.4, (layout, name, e)
        entry = rep["lasers"][name]
        assert entry["n_observations"] >= 3 and entry["n_points"] > 0 and entry["region_used"] == "margin"
        assert rep["residuals"][name]["median_abs_mm"] < 0.05
    # per-frame counts per configured name, with the region split
    rows = [r for r in rep["per_frame"] if r["board"]]
    assert all(set(r["points"]) == set(FOUR) and set(r["points"]["vertical_2"]) == {"board", "margin"} for r in rows)
    for name in FOUR:
        assert sum(r["points"][name]["margin"] > 0 for r in rows) >= 3, name
    # against the previous calibration: per name
    planes2, rep2 = lasers_from_recording(rec, true, spec, names=FOUR)
    assert set(rep2["change_vs_input"]) == set(FOUR)
    assert all(rep2["change_vs_input"][n]["angle_deg"] < 0.03 for n in FOUR)


def test_four_lasers_health_check(four_set):
    layout, rec, true, spec, _ = four_set
    chk = check_from_recording(rec, true, spec)
    assert chk["ok"], chk["verdict"]
    assert set(chk["lasers"]["lasers"]) == set(FOUR)
    for name in FOUR:
        assert chk["lasers"]["lasers"][name]["median_abs_mm"] < 0.1, (name, chk["lasers"]["lasers"][name])
    moved = ScannerCalibration(true.stereo, dict(true.lasers), true.imu)
    p = moved.lasers["horizontal_2"]
    moved.lasers["horizontal_2"] = LaserPlane("horizontal_2", G.so3_exp([np.radians(1.0), 0, 0]) @ p.normal, p.d)
    bad = check_from_recording(rec, moved, spec)
    assert not bad["ok"] and "horizontal_2" in bad["verdict"] and bad["lasers"]["lasers"]["horizontal"]["ok"]


def test_two_lasers_configured_on_a_four_laser_build_is_reported(four_set):
    """The setting says two lasers but four are drawn: a family's union then holds two sheets.
    The fit takes the best plane, as the two-laser fit always did; the second line's points are
    outliers and the report shows it (inlier fraction about one half)."""
    layout, rec, true, spec, _ = four_set
    if layout != "4":
        pytest.skip("one layout is enough")
    planes, rep = lasers_from_recording(rec, stereo_only(true), spec, names=TWO)
    for name in TWO:
        if name in planes:
            assert rep["lasers"][name]["inlier_fraction"] < 0.8, (name, rep["lasers"][name]["inlier_fraction"])


def test_board_near_where_the_lines_of_a_family_meet(tmp_path):
    """Layout "4-cross250": all four sheets cross the cameras' centre line about 254 mm ahead of the
    left camera, so on a board held there square to the camera the two lines of each family lie on top
    of each other; where the extraction cannot drop the merged stripe, the fit leaves it out (points
    within ``FAMILY_CLEAR_MM`` of the other sheet). Those views simply give fewer points, and the
    planes stay as good as without them. The other views are the 8 of the ``four_set`` fixture."""
    layout = "4-cross250"
    calib = nominal_calibration(layout=layout)
    spec = CharucoSpec()
    W, H = spec.size_mm
    meet = []
    for k, (rx, ry) in enumerate([(0.0, 0.0), (2.0, -1.0), (-1.5, 2.5)]):
        R = G.so3_exp(np.radians([rx, ry, 4.0 * k]))
        meet.append(G.make_se3(R, np.array([30.0, 0.0, 254.0]) - R @ np.array([W / 2, H / 2, 0.0])))
    base = laser_calib_board_poses(8, calib, None, seed=1, layout=layout)
    pairs, true, _, sd = cached_result(make_laser_calib_set, realistic=False, seed=1, layout=layout, poses=base + meet)
    rec = write_pairs(tmp_path / "rec", pairs, purpose="laser_calibration")
    planes, rep = lasers_from_recording(rec, stereo_only(true), CharucoSpec(**sd), names=FOUR)
    assert rep["ok"] and set(planes) == set(FOUR), {n: e.get("reason") for n, e in rep["lasers"].items()}
    err = plane_errors(planes, true.lasers)
    metrics("lasers4_meeting", **{f"{n}_{k}": v for n, e in err.items() for k, v in e.items()},
            **{f"{n}_views": rep["lasers"][n]["n_observations"] for n in FOUR},
            **{f"{f}_points_left_out": rep["families"][f]["n_points_near_other_sheet"] for f in ("vertical", "horizontal")})
    for name in FOUR:
        e = err[name]
        assert e["angle_deg"] < 0.03 and abs(e["d_err_mm"]) < 0.1, (name, e)
        assert e["point_err_median_mm"] < 0.05 and e["point_err_max_mm"] < 0.4, (name, e)
    # the three views at the meeting depth hold far fewer usable points of each line than the
    # others (the most tilted one keeps some: there the lines move apart toward the board edges)
    rows = rep["per_frame"]
    for name in FOUR:
        n = [r["points"][name]["margin"] for r in rows]
        assert sum(n[8:]) < 1.5 * np.median(n[:8]), (name, n)
    assert all(rep["families"][f]["n_points_near_other_sheet"] > 0 for f in ("vertical", "horizontal"))


# --------------------------------------------------------------------------- capture
def test_capture_counts_per_configured_line(four_set):
    layout, rec, true, spec, pairs = four_set
    # with the planes of the calibration in use, every line is named (on the white margin)
    proc = BoardProcessor("lasers", spec, true, lines=FOUR)
    proc.start()
    views = [proc.process(StereoFrame(i, 0.0, *pair)) for i, pair in enumerate(pairs)]
    assert all(bv.T_cb is not None and set(bv.line_counts) == set(FOUR) for bv in views)
    good = [i for i, bv in enumerate(views) if all(bv.line_counts[n] >= LINE_MIN_POINTS for n in FOUR)]
    assert len(good) >= 3, [bv.line_counts for bv in views]
    bv = views[good[0]]
    assert missing_lines(bv, FOUR) == []
    frame = StereoFrame(0, 0.0, *pairs[good[0]])
    # a first laser calibration has no planes yet: the lines of a family are counted, not named
    proc = BoardProcessor("lasers", spec, stereo_only(true), lines=FOUR)
    proc.start()
    bv2 = proc.process(frame)
    assert set(bv2.line_counts) == set() and len(bv2.family_counts["vertical"]) == 2
    assert all(min(c) >= LINE_MIN_POINTS for c in bv2.family_counts.values()), bv2.family_counts
    assert missing_lines(bv2, FOUR) == []
    # two lasers configured: as before
    proc = BoardProcessor("lasers", spec, true, lines=TWO)
    proc.start()
    assert set(proc.process(frame).line_counts) == set(TWO)


def test_capture_warns_about_a_missing_line(tmp_path):
    """Four lasers configured, but the view shows only two (a two-laser rendering)."""
    pairs, true2, _, sd = cached_result(make_laser_calib_set, n_views=6, realistic=False, seed=1)
    true4 = nominal_calibration(layout="4")
    spec = CharucoSpec(**sd)
    frame = StereoFrame(0, 0.0, *pairs[0])
    proc = BoardProcessor("lasers", spec, true4, lines=FOUR)
    proc.start()
    bv = proc.process(frame)
    miss = missing_lines(bv, FOUR)
    assert any("vertical_2" in m for m in miss) and any("horizontal_2" in m for m in miss)
    assert not any(m.startswith("vertical ") or m.startswith("horizontal ") for m in miss), miss
    proc = BoardProcessor("lasers", spec, stereo_only(true4), lines=FOUR)
    proc.start()
    miss = missing_lines(proc.process(frame), FOUR)
    assert any("1 of 2 vertical" in m for m in miss) and any("1 of 2 horizontal" in m for m in miss), miss
    proc = BoardProcessor("lasers", spec, true2, lines=TWO)
    proc.start()
    assert missing_lines(proc.process(frame), TWO) == []


def test_merge_and_run_flow_take_the_laser_set(four_set, tmp_path):
    from ohs.app import capture

    layout, rec, true, spec, _ = four_set
    if layout != "4":
        pytest.skip("one layout is enough")
    value, rep = capture.run_flow("lasers", rec, spec, stereo_only(true), lasers=FOUR)
    assert set(value) == set(FOUR) and rep["names"] == list(FOUR)
    some = {n: value[n] for n in ("vertical", "horizontal", "vertical_2")}
    merged, notes = capture.merge_calibration(stereo_only(true), "lasers", some, lasers=FOUR)
    assert set(merged.lasers) == set(some) and notes == ["the horizontal_2 plane was not fitted: there is no plane for it"]
    merged, notes = capture.merge_calibration(true, "lasers", some, lasers=FOUR)
    assert set(merged.lasers) == set(FOUR) and notes == ["the horizontal_2 plane was not fitted: kept the previous one"]
    assert capture.merge_calibration(true, "lasers", some)[1] == []  # two lasers by default: both were fitted
