"""The CPU path's defaults (MAINTENANCE_PLAN F14, the owner's decision D7 of 2026-11-20; ``ohs.app.pipeline``,
"The CPU path's defaults").

With the CPU matcher and the Open3D tracking model the pipeline turns on, where nothing is set by hand: the
matcher's flip average (with the 2 px hole rule of detail mode) and the ray cast's weight threshold 8 at
full resolution, and the velocity guard at every size. Everywhere else, the GPU path above all, the
settings are what they were before. Each can still be turned off.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs.app.pipeline import FramePipeline, PipelineParams, default_depth_params, default_fusion_params  # noqa: E402
from ohs.sim.nominal import nominal_calibration  # noqa: E402
from ohs.track.markerless import VELOCITY_MAX_ACCEL  # noqa: E402


def _cpu(scale, depth=None, fusion=None):
    d = replace(depth or default_depth_params(scale), backend="cpu")
    f = replace(fusion or default_fusion_params(scale), model_backend="open3d")
    return PipelineParams(scale=scale, use_lasers=False, depth=d, fusion=f)


def _settings(pipe):
    tf = pipe.track_fusion_params
    return (pipe.depth_params.flip_average, tf.velocity_max_accel, tf.raycast_weight_threshold, pipe.detail_hole_px)


def test_the_cpu_path_turns_the_three_on_where_nothing_is_set():
    calib = nominal_calibration()
    with FramePipeline(calib, _cpu(1.0)) as pipe:
        assert pipe.depth_estimator.backend == "cpu" and pipe.model_backend == "open3d"
        assert _settings(pipe) == (True, VELOCITY_MAX_ACCEL, 8.0, 2)
        # what the trackers use and what a report shows
        assert pipe.tracker.params == pipe.track_fusion_params and pipe.depth_estimator.params.flip_average
        assert (pipe.fusion_params.velocity_max_accel, pipe.fusion_params.raycast_weight_threshold) == (VELOCITY_MAX_ACCEL, 8.0)
    # half resolution: the guard only (the 8-frame 640 x 400 recording is bit for bit as before with it)
    with FramePipeline(calib, _cpu(0.5)) as pipe:
        assert _settings(pipe) == (False, VELOCITY_MAX_ACCEL, 3.0, 3)


def test_each_of_them_can_still_be_turned_off():
    calib = nominal_calibration()
    off = _cpu(1.0, depth=replace(default_depth_params(1.0), flip_average=False),
               fusion=replace(default_fusion_params(1.0), velocity_max_accel=0.0, raycast_weight_threshold=3.0))
    with FramePipeline(calib, off) as pipe:
        assert _settings(pipe) == (False, 0.0, 3.0, 6)


def test_elsewhere_the_settings_are_as_before():
    """The GPU matcher with the Open3D model, the single-model tracking, marker mode, fusing at given poses,
    and the GPU path."""
    calib = nominal_calibration()
    before = (False, None, 3.0)
    p = PipelineParams(use_lasers=False, depth=replace(default_depth_params(1.0), backend="gpu"),
                       fusion=replace(default_fusion_params(1.0), model_backend="open3d"))
    with FramePipeline(calib, p) as pipe:
        if pipe.depth_estimator.backend == "gpu":
            assert _settings(pipe)[:3] == before and pipe.detail_hole_px == 6
    with FramePipeline(calib, replace(_cpu(1.0), track_filter_px=0.0)) as pipe:
        assert not pipe.dual_model and _settings(pipe)[:3] == before
        assert (pipe.tracker.params.velocity_max_accel, pipe.tracker.params.raycast_weight_threshold) == (None, 3.0)
    with FramePipeline(calib, replace(_cpu(1.0), mode="markers")) as pipe:
        assert pipe.depth_params.flip_average is False and pipe.fusion_params.raycast_weight_threshold == 3.0
    with FramePipeline(calib, replace(_cpu(1.0), tracking=False)) as pipe:
        assert pipe.depth_params.flip_average is False and pipe.fusion_params.velocity_max_accel is None
    with FramePipeline(calib, PipelineParams(use_lasers=False)) as pipe:  # the defaults: the GPU path where there is one
        if pipe.depth_estimator.backend != "gpu" or pipe.model_backend != "gpu":
            pytest.skip("no GPU here")
        assert _settings(pipe)[:3] == before and pipe.detail_hole_px == 6
        assert (pipe.fusion_params.velocity_max_accel, pipe.fusion_params.raycast_weight_threshold) == (None, 3.0)


@pytest.mark.slow
def test_with_the_defaults_a_dark_part_does_not_slide_on_the_cpu_path(tmp_path_factory):
    """The first 12 frames of a dark bracket (H2's ``bracket-dark`` scene, seed 12), the CPU matcher and the
    Open3D model and nothing else set. Before the defaults: 0, 10, 43, 79, 114, 149 ... 307 mm."""
    from ohs import geometry as G
    from ohs.io.recording import RecordingReader
    from recordings import cached_recording
    from tools import calib_health_study as study

    spec = dict(study.RECORDINGS["bracket-dark"], seed=12, frames=12)
    rec = cached_recording(tmp_path_factory.mktemp("f14") / "dark", study.make_recording, spec=spec)
    rr = RecordingReader(rec)
    gt = rr.ground_truth_poses()
    with FramePipeline(rr.calibration(), _cpu(1.0)) as pipe:
        pipe.add_imu(rr.imu_samples())
        res = [pipe.process(rr.frame(i)) for i in range(len(rr.times()))]
    T0 = G.invert_se3(gt[0])
    err = [float(np.linalg.norm(r.track.T_wc[:3, 3] - (T0 @ gt[k])[:3, 3])) for k, r in enumerate(res)]
    assert all(r.track.ok for r in res) and err[-1] < 5.0 and max(err[3:]) < 5.0, np.round(err, 1).tolist()
