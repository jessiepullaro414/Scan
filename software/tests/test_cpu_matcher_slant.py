"""The CPU matcher on surfaces that slant up and down the image (MAINTENANCE_PLAN F14).

OpenCV's SGBM (all its single-pass modes, the default 3-way one included) sums its path costs only from
above, the left and the right: no path comes up from below. Between the projected dots, where the
matching cost has no texture, a pixel takes the disparity of the pixels above it. On a surface whose
disparity grows down the image (a bench seen from above at an angle, the usual view) that is too small a
disparity, so the depth is too far: 0.07-0.08 px on the simulated scans at 1280 x 800 (+0.10 to +0.14 mm),
over the whole image. ``DepthParams.flip_average`` (off by default) also matches the pair upside down,
where the same paths come from below, and averages the two (``ohs.vision.depth``, "Paths from above
only"). The GPU matcher sums its paths both ways and ignores the option.

The planes are synthetic (no simulator): sparse dots, 1280 x 800, a plane whose disparity changes by
``slope`` px a row. Measured 2026-11-20 with the 3-way SGBM and the sub-pixel step: mean disparity error
-0.062 px at +0.05 px a row and +0.071 px at -0.05 px a row; with ``flip_average`` under 0.02 px.
The first test asserts the bias is there with the option off, so that a change of the default or of
OpenCV that removes it is noticed (and this note and the docstrings revised).
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from ohs.vision.depth import DepthEstimator, DepthParams
from test_vision_depth import B, make_pair, overlap_mask

FULL = DepthParams(block_size=7, speckle_window_size=200, backend="cpu")  # the pipeline's 1280 x 800 settings


def _mean_error(slope: float, params: DepthParams) -> float:
    rng = np.random.default_rng(31 if slope > 0 else 32)
    f = 720.0
    b = slope / (f * B)  # d = f B / z, so d(v) = d0 + slope * (v - 400)
    inv_z = lambda u, v: 1 / 300.0 + 0 * np.asarray(u, float) + b * (np.asarray(v, float) - 400.0)  # noqa: E731
    left, right, _z, d_true, K = make_pair(1.0, inv_z, rng, vertical=False)
    est = DepthEstimator(K, B, params)
    assert est.backend == "cpu"
    d = est.disparity(left, right).astype(np.float64)
    e = (d - d_true)[overlap_mask(d_true) & np.isfinite(d)]
    e = e[np.abs(e) < 1.0]
    assert len(e) > 0.6 * d.size
    return float(e.mean())


@pytest.mark.parametrize("slope", [0.05, -0.05])
def test_the_3way_matcher_lags_on_a_surface_slanted_up_and_down_the_image(slope):
    """Option off (the default): the bias is there, with the sign of the slope (too far where the disparity
    grows downwards). Measured -0.062 / +0.071 px."""
    assert not DepthParams().flip_average
    m = _mean_error(slope, FULL)
    assert 0.04 < abs(m) < 0.10 and np.sign(m) == -np.sign(slope), m


@pytest.mark.parametrize("slope", [0.05, -0.05])
def test_flip_average_takes_the_lag_out(slope):
    m = _mean_error(slope, replace(FULL, flip_average=True))
    assert abs(m) < 0.02, m


def test_flip_average_leaves_the_gpu_matcher_alone():
    rng = np.random.default_rng(33)
    left, right, _z, _d, K = make_pair(0.5, lambda u, v: np.full_like(np.asarray(u, float), 1 / 250.0), rng)
    g_off = DepthEstimator(K, B, DepthParams(backend="gpu"))
    if g_off.backend != "gpu":
        pytest.skip("no GPU here")
    g_on = DepthEstimator(K, B, DepthParams(backend="gpu", flip_average=True))
    assert np.array_equal(g_on.disparity(left, right), g_off.disparity(left, right), equal_nan=True)


@pytest.mark.slow
def test_with_flip_average_a_dark_part_does_not_slide_on_the_cpu_path(tmp_path_factory):
    """The first 12 frames of a dark bracket (H2's ``bracket-dark`` scene, seed 12), CPU matcher and Open3D
    model, the option on. With it detail mode leaves out only 2 px around the matcher's holes instead of 6
    (``ohs.app.pipeline.DETAIL_HOLE_PX_FLIP_1280``). Off (today's default) the pose slides 35 mm a frame
    from the second frame on and ends 307 mm off: the 6 px leave the dark part almost without depth."""
    from ohs import geometry as G
    from ohs.app.pipeline import FramePipeline, PipelineParams, default_depth_params, default_fusion_params
    from ohs.io.recording import RecordingReader
    from recordings import cached_recording
    from tools import calib_health_study as study

    spec = dict(study.RECORDINGS["bracket-dark"], seed=12, frames=12)
    rec = cached_recording(tmp_path_factory.mktemp("f14") / "dark", study.make_recording, spec=spec)
    rr = RecordingReader(rec)
    gt = rr.ground_truth_poses()
    T0 = G.invert_se3(gt[0])
    errs = {}
    for flip in (False, True):
        pp = PipelineParams(depth=replace(default_depth_params(1.0), backend="cpu", flip_average=flip), use_lasers=False,
                            fusion=replace(default_fusion_params(1.0), model_backend="open3d"))
        with FramePipeline(rr.calibration(), pp) as pipe:
            pipe.add_imu(rr.imu_samples())
            res = [pipe.process(rr.frame(i)) for i in range(len(rr.times()))]
            assert pipe.depth_estimator.backend == "cpu" and pipe.model_backend == "open3d"
            assert pipe.detail_hole_px == (2 if flip else 6)
        assert all(r.track.ok for r in res)
        errs[flip] = [float(np.linalg.norm(r.track.T_wc[:3, 3] - (T0 @ gt[k])[:3, 3])) for k, r in enumerate(res)]
    # off: 0, 10, 43, 79, 114, 149 ... 307 mm (asserted so that a change there is noticed); on: the second frame
    # is still 11 mm off (a young Open3D model), from the fourth on 0.5-1.4 mm
    assert errs[False][-1] > 100.0, np.round(errs[False], 1).tolist()
    assert max(errs[True][3:]) < 5.0, np.round(errs[True], 1).tolist()
