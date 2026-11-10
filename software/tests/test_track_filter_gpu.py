"""Tests for ohs.track.filter_gpu.TrackingFilterGpu: the tracking filter on the GPU gives the CPU
filter's map (ohs.app.pipeline.tracking_depth) to float rounding, and the pipeline uses it. Skipped
without a GPU."""
import numpy as np
import pytest

from ohs import compute
from ohs.app.pipeline import FramePipeline, PipelineParams, tracking_depth
from ohs.sim.nominal import nominal_calibration
from ohs.track.filter_gpu import TrackingFilterGpu


@pytest.fixture(scope="module")
def dev():
    d = compute.get_device()
    if d is None:
        pytest.skip(f"no GPU: {compute.status()}")
    return d


def scene(w: int, h: int, seed: int) -> np.ndarray:
    vv, uu = np.mgrid[0:h, 0:w]
    rng = np.random.default_rng(seed)
    d = (300.0 + 0.2 * uu + 0.1 * vv + rng.normal(0, 0.6, (h, w))).astype(np.float32)
    d[:, int(0.62 * w):] -= 40.0  # an occlusion step
    d[h // 3:h // 3 + 20, w // 6:w // 6 + 30] = 0.0  # a hole
    d[h // 2:h // 2 + 3, w // 2:w // 2 + 4] = np.nan  # no depth, as the matcher marks it
    return d


@pytest.mark.parametrize("size,work,sigma", [((640, 400), (320, 200), 6.0), ((640, 400), (640, 400), 3.0),
                                             ((960, 600), (320, 200), 6.0)])
def test_gpu_filter_matches_the_cpu_filter(dev, size, work, sigma):
    w, h = size
    d = scene(w, h, 3)
    cpu = tracking_depth(d, sigma, work_size=work)
    f = TrackingFilterGpu(dev, size, work, sigma)
    try:
        gpu = f(d)
    finally:
        f.close()
    assert gpu.shape == d.shape and gpu.dtype == np.float32 and not np.isnan(gpu).any()
    a, b = cpu > 0, gpu > 0
    assert a.mean() > 0.3 and (a ^ b).mean() < 1e-4  # the same pixels, but for float rounding
    assert np.abs(cpu[a & b] - gpu[a & b]).max() < 1e-3
    assert not b[:, int(0.62 * w) - 4:int(0.62 * w) + 4].any() and not b[:3].any()  # eroded at the step and border


def test_supported_sizes():
    assert TrackingFilterGpu.supports((1280, 800), (640, 400))
    assert TrackingFilterGpu.supports((640, 400), (640, 400))
    assert not TrackingFilterGpu.supports((1280, 800), (500, 400))  # not a whole multiple
    assert not TrackingFilterGpu.supports((1280, 800), (640, 200))  # not the same factor both ways


def test_the_pipeline_filters_on_the_gpu(dev):
    with FramePipeline(nominal_calibration(), PipelineParams(scale=0.5, use_lasers=False, use_imu=False)) as p:
        d = scene(*p.image_size, 4)
        out = p.tracking_input(d)
        assert isinstance(p._track_filter_gpu, TrackingFilterGpu)
        cpu = tracking_depth(d, p.track_filter_px, work_size=p.track_size)
        assert ((out > 0) ^ (cpu > 0)).mean() < 1e-4
