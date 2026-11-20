"""The CPU matcher's depth in detail mode (MAINTENANCE_PLAN F13).

A detailed part (the simulated bracket) is tracked on the filtered depth plus the raw depth wherever
the filter dropped a pixel (``ohs.app.pipeline``, "Detailed parts"). The CPU matcher's (SGBM) raw depth
next to a pixel without depth is biased (too far, by +0.3 to +0.5 mm within a few pixels against
+0.19 mm elsewhere on the bracket), and its holes sit where the projected dots leave gaps, which move
with the scanner. Fed to ICP, that camera-fixed error pulls every frame towards the previous pose: the
tracker turned 4-7 % less than the scanner every frame, and the 90-frame bracket ended 3.6 deg and
9.4 mm off. Detail mode therefore leaves the CPU matcher's raw depth out within
``DETAIL_HOLE_PX_1280`` px (at 1280 px width) of a pixel without depth. The GPU matcher's depth is
used as before.

The scan is the 12-frame full-resolution bracket of ``test_calib_health_scans.py`` (the same
arguments: one render, shared by the cache), matched on the CPU in both runs of the suite: with a
GPU the tracking model is then the GPU volume, with ``OHS_COMPUTE=cpu`` Open3D's.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.app.pipeline import FramePipeline, PipelineParams, default_depth_params  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from recordings import scan_recording  # noqa: E402

N = 12


@pytest.fixture(scope="module")
def short_scan(tmp_path_factory):
    return scan_recording(tmp_path_factory.mktemp("f13") / "rec", scene="bracket", n_frames=N, image_size=(1280, 800),
                          realistic=True, seed=7, depth_every=0)


def test_hole_borders_are_left_out_of_the_cpu_matchers_detail_depth():
    from ohs.app.pipeline import DETAIL_HOLE_PX_1280, with_raw_depth, without_hole_borders
    from ohs.sim.nominal import nominal_calibration

    d = np.full((40, 60), 300.0, np.float32)
    d[20, 30] = 0.0  # one hole
    d[5, 50] = np.nan
    out = without_hole_borders(d, 3)
    gone = out == 0
    assert gone[17:24, 27:34].all() and gone.sum() == 49 + 49  # 7 x 7 around each hole, the holes included
    assert np.isfinite(out).all() and d[20, 31] == 300.0  # the input is left alone
    assert without_hole_borders(np.full((5, 5), 300.0, np.float32), 3).min() == 300.0  # the image border is no hole
    assert without_hole_borders(d, 0) is not None and np.array_equal(without_hole_borders(d, 0), d, equal_nan=True)
    for backend, px in (("cpu", round(DETAIL_HOLE_PX_1280 / 2)), ("gpu", None)):
        pp = PipelineParams(scale=0.5, use_lasers=False, depth=replace(default_depth_params(0.5), backend=backend))
        with FramePipeline(nominal_calibration(), pp) as pipe:
            raw = np.full((400, 640), 300.0, np.float32)
            raw[200, 300] = 0.0
            got = with_raw_depth(np.zeros_like(raw), pipe._detail_raw(raw))
            if pipe.depth_estimator.backend == "gpu":
                assert np.array_equal(got, raw)  # the GPU matcher's depth as it is
            elif backend == "cpu":
                assert pipe.detail_hole_px == px == 3 and (got == 0).sum() == 49


@pytest.mark.slow
def test_the_cpu_matcher_does_not_pull_a_detailed_scan_towards_standing_still(short_scan):
    rr = RecordingReader(short_scan)
    gt = rr.ground_truth_poses()
    pp = PipelineParams(depth=replace(default_depth_params(1.0), backend="cpu"), use_lasers=False)
    with FramePipeline(rr.calibration(), pp) as pipe:
        pipe.add_imu(rr.imu_samples())
        res = [pipe.process(rr.frame(i)) for i in range(N)]
        assert pipe.depth_estimator.backend == "cpu" and pipe.tracker.detail_mode  # what this test is about
    assert all(r.track.ok for r in res)
    T0 = G.invert_se3(gt[0])
    true = [T0 @ gt[k] for k in range(N)]
    # each frame's turn against the scanner's, along the scanner's (negative: the tracker turned less)
    pull = []
    for k in range(1, N):
        wt = G.so3_log(true[k - 1][:3, :3].T @ true[k][:3, :3])
        we = G.so3_log(res[k - 1].track.T_wc[:3, :3].T @ res[k].track.T_wc[:3, :3])
        pull.append(float((we - wt) @ wt / (wt @ wt)))
    final = G.rotation_angle_deg(true[-1][:3, :3].T @ res[-1].track.T_wc[:3, :3])
    # before the fix: a median of -3.7 % and 0.32 deg (Open3D model), -2.7 % and 0.18 deg (GPU model);
    # with it -1.4 % and 0.10 deg, -1.2 % and 0.07 deg
    assert np.median(pull) > -0.02 and final < 0.15, (np.round(pull, 3).tolist(), final)
