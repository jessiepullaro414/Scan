"""Tests for ohs.track.filter_gpu.TrackingFilterGpu: the tracking filter on the GPU gives the CPU
filter's map (ohs.app.pipeline.tracking_depth) to float rounding, and the pipeline uses it. Skipped
without a GPU."""
import numpy as np
import pytest

from ohs import compute
from ohs.app.pipeline import FramePipeline, PipelineParams, tracking_depth
from ohs.io.recording import RecordingReader
from ohs.sim.datasets import make_scan_recording
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


def test_start_and_finish_give_the_same_map(dev):
    """Started ahead and collected later, from the array or from a device buffer that holds it:
    the map of a plain call, bit for bit."""
    cl = dev.cl
    d = scene(640, 400, 5)
    f = TrackingFilterGpu(dev, (640, 400), (320, 200), 6.0)
    try:
        want = f(d)
        got = f.finish(f.start(d))
        assert got.tobytes() == want.tobytes() and got is not want
        buf = cl.Buffer(dev.ctx, cl.mem_flags.READ_WRITE | cl.mem_flags.COPY_HOST_PTR, hostbuf=d)
        pending = f.start(buf)
        d2 = d.copy()
        d2[:] = 7.0  # the array is not read again
        assert f.finish(pending).tobytes() == want.tobytes()
        assert f(scene(640, 400, 6)).tobytes() != want.tobytes() and f(d).tobytes() == want.tobytes()
        with pytest.raises(ValueError):
            f.start(cl.Buffer(dev.ctx, cl.mem_flags.READ_WRITE, size=16))
        buf.release()
    finally:
        f.close()


def test_the_pipeline_tracks_on_the_filter_of_its_depth(dev, tmp_path):
    """What the tracker gets for a frame is the GPU filter of that frame's depth map, bit for bit,
    however the pipeline hands the map to the filter."""
    rec = make_scan_recording(tmp_path / "rec", n_frames=4, image_size=(640, 400), realistic=True, seed=4,
                              depth_every=0)
    reader = RecordingReader(rec)
    with FramePipeline(reader.calibration()) as p:
        seen = []
        track = p.tracker.track

        def spy(depth, *args, **kwargs):
            seen.append(depth.copy())
            return track(depth, *args, **kwargs)

        p.tracker.track = spy
        p.add_imu(reader.imu_samples())
        res = [p.process(reader.frame(i)) for i in range(4)]
        assert p.depth_estimator.backend == "gpu" and isinstance(p._track_filter_gpu, TrackingFilterGpu)
        assert len(seen) == 4 and all(r.track.ok and "track_filter" in r.timings_ms for r in res)
        f = TrackingFilterGpu(dev, p.image_size, p.track_size, p.track_filter_px)
        try:
            for r, got in zip(res, seen):
                assert got.tobytes() == f(r.depth).tobytes()
        finally:
            f.close()


def test_the_pipeline_filters_on_the_gpu(dev):
    with FramePipeline(nominal_calibration(), PipelineParams(scale=0.5, use_lasers=False, use_imu=False)) as p:
        d = scene(*p.image_size, 4)
        out = p.tracking_input(d)
        assert isinstance(p._track_filter_gpu, TrackingFilterGpu)
        cpu = tracking_depth(d, p.track_filter_px, work_size=p.track_size)
        assert ((out > 0) ^ (cpu > 0)).mean() < 1e-4
