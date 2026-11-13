"""Tests for the GPU depth backend (ohs.vision.depth_gpu behind DepthEstimator).

* Backend selection: ``DepthParams.backend`` ("auto" / "cpu" / "gpu"), ``OHS_COMPUTE=cpu`` with
  ``ohs.compute.reset()``, the WLS exception and the run-time fallback to the CPU.
* Accuracy: GPU against CPU against the simulator's true depth on small realistic frames
  (``make_scan_recording``, rectified as the pipeline does), and on the analytic planes of
  test_vision_depth.
* The OpenCV twins: the GPU's 3x3 median and speckle filter equal ``cv2.medianBlur`` and
  ``cv2.filterSpeckles`` exactly.

Everything that needs a GPU is skipped (``pytest.skip``) when ``ohs.compute.get_device()`` finds
none, so CPU-only machines and ``OHS_COMPUTE=cpu`` runs pass.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np
import pytest

from ohs import compute
from ohs.app.pipeline import default_depth_params, effective_scale
from ohs.vision.depth import DepthEstimator, DepthParams, disparity_range
from test_vision_depth import B, check, make_pair, overlap_mask


@pytest.fixture
def gpu():
    dev = compute.get_device()
    if dev is None:
        pytest.skip(f"no GPU: {compute.status()}")
    return dev


@pytest.fixture
def cpu_only(monkeypatch):
    """``OHS_COMPUTE=cpu`` for one test; the device choice is forgotten before and after."""
    monkeypatch.setenv(compute.ENV_VAR, "cpu")
    compute.reset()
    yield
    monkeypatch.delenv(compute.ENV_VAR, raising=False)
    compute.reset()


def plane(scale: float, z: float, seed: int):
    rng = np.random.default_rng(seed)
    return make_pair(scale, lambda u, v: np.full_like(np.asarray(u, float), 1.0 / z), rng)


# --------------------------------------------------------------------------- backend selection
def test_backend_choice(gpu):
    left, right, z_true, d_true, K = plane(0.5, 260.0, 1)
    est = DepthEstimator(K, B)
    assert est.backend == "gpu" and est.device == gpu.name and est.params.backend == "auto"
    assert "GPU" in est.backend_note
    cpu = DepthEstimator(K, B, DepthParams(backend="cpu"))
    assert cpu.backend == "cpu" and cpu.device is None
    # the same search range either way
    assert (est.min_disparity, est.num_disparities) == (cpu.min_disparity, cpu.num_disparities)
    forced = DepthEstimator(K, B, DepthParams(backend="gpu"))
    assert forced.backend == "gpu"
    # WLS needs OpenCV's SGBM: the CPU, and the note says why
    wls = DepthEstimator(K, B, DepthParams(wls=True))
    assert wls.backend == "cpu" and "WLS" in wls.backend_note
    with pytest.raises(ValueError):
        DepthParams(backend="cuda")


def test_ohs_compute_cpu_turns_the_gpu_off(cpu_only, caplog):
    left, right, z_true, d_true, K = plane(0.5, 260.0, 2)
    assert compute.get_device() is None and "turned off" in compute.status()
    est = DepthEstimator(K, B)
    assert est.backend == "cpu" and est.device is None and "turned off" in est.backend_note
    with caplog.at_level(logging.WARNING, logger="ohs.vision.depth"):
        forced = DepthEstimator(K, B, DepthParams(backend="gpu"))
    assert forced.backend == "cpu"
    assert any("backend is \"gpu\"" in r.getMessage() for r in caplog.records)
    check(forced.compute(left, right), z_true, d_true)


def test_reset_brings_the_gpu_back(gpu, monkeypatch):
    left, right, z_true, d_true, K = plane(0.5, 260.0, 3)
    monkeypatch.setenv(compute.ENV_VAR, "cpu")
    compute.reset()
    try:
        assert DepthEstimator(K, B).backend == "cpu"
    finally:
        monkeypatch.delenv(compute.ENV_VAR, raising=False)
        compute.reset()
    assert DepthEstimator(K, B).backend == "gpu"


def test_runtime_failure_falls_back_to_the_cpu(gpu, caplog):
    left, right, z_true, d_true, K = plane(0.5, 260.0, 4)
    est = DepthEstimator(K, B)
    assert est.backend == "gpu"

    def broken(*a, **k):
        raise RuntimeError("simulated device loss")

    est._gpu.depth = broken
    with caplog.at_level(logging.WARNING, logger="ohs.vision.depth"):
        depth = est.compute(left, right)
    assert est.backend == "cpu" and est.device is None and "simulated device loss" in est.backend_note
    assert any("continuing on the CPU" in r.getMessage() for r in caplog.records)
    check(depth, z_true, d_true)


def test_depth_without_exact_division_is_converted_on_the_cpu(gpu):
    left, right, z_true, d_true, K = plane(0.5, 300.0, 5)
    est = DepthEstimator(K, B)
    exact = est.compute(left, right)
    est._gpu.exact_division = False
    assert est._gpu.depth(left, right) is None
    assert np.array_equal(est.compute(left, right), exact)


def test_compute_with_sigma_equals_the_three_array_download(gpu):
    """Skipping the disparity download changes no depth and no sigma, with and without exact division."""
    left, right, z_true, d_true, K = plane(0.5, 300.0, 6)
    est = DepthEstimator(K, B)
    m = est._gpu
    disp, depth, sigz = m._run(left, right, None, None, "disp", "depth", "sigz")  # the lower level: all three
    if m.exact_division:
        d, s = est.compute_with_sigma(left, right)
        assert np.array_equal(d, depth) and np.array_equal(s, sigz)
    m.exact_division = False  # converted on the CPU from the disparity, as today
    want_d = est.depth_from_disparity(disp)
    want_s = np.where(want_d > 0, sigz, np.float32(0.0)).astype(np.float32)
    d, s = est.compute_with_sigma(left, right)
    assert np.array_equal(d, want_d) and np.array_equal(s, want_s)


def test_depth_gpu_constants_are_the_cpu_modules():
    """One copy of the constants the two modules share (importing depth_gpu needs no GPU)."""
    from ohs.vision import depth, depth_gpu
    assert depth_gpu.LK_EPS2 is depth.LK_EPS2 and depth_gpu.LK_MIN_TEXTURE is depth.LK_MIN_TEXTURE
    assert depth_gpu.GPU_MIN_SIZE is depth.GPU_MIN_SIZE
    assert (depth.LK_EPS2, depth.LK_MIN_TEXTURE, depth.GPU_MIN_SIZE) == (16.0, 0.02, 32)
    assert depth._LKRefiner._EPS2 == depth.LK_EPS2 and depth._LKRefiner._MIN_TEXTURE == depth.LK_MIN_TEXTURE


# --------------------------------------------------------------------------- contract and accuracy
def test_gpu_api_contract(gpu):
    left, right, z_true, d_true, K = plane(0.5, 300.0, 51)
    est = DepthEstimator(K, B, DepthParams(backend="gpu"))
    disp = est.disparity(left, right)
    assert disp.dtype == np.float32 and disp.shape == left.shape and np.isnan(disp).any()
    assert np.nanmin(disp) >= est.min_disparity - 1 and np.nanmax(disp) <= est.max_disparity + 1
    depth = est.compute(left, right)
    assert depth.dtype == np.float32 and depth.shape == left.shape
    # the GPU's depth is the CPU conversion of its disparity, bit for bit (correctly rounded division)
    assert np.array_equal(depth, est.depth_from_disparity(disp))
    assert np.all((depth == 0) | ((depth >= 150) & (depth <= 450)))
    # pixels whose match window would leave the right image are invalid
    cols = np.arange(left.shape[1], dtype=np.float32)[None, :]
    assert not np.any(np.isfinite(disp) & (cols - disp < est.params.block_size // 2 - 1.0))
    with pytest.raises(ValueError):
        est.compute(left, right[:, :-1])


@pytest.mark.parametrize("scale", [0.5, 1.0])
def test_gpu_matches_cpu_on_planes(gpu, scale):
    w = {0.5: 640, 1.0: 1280}[scale]
    rng = np.random.default_rng(11)
    inv_z = lambda u, v: 1 / 220.0 + (1 / 380.0 - 1 / 220.0) * np.asarray(u, float) / w + 2e-7 * np.asarray(v, float)  # noqa: E731
    left, right, z_true, d_true, K = make_pair(scale, inv_z, rng, vertical=True, horizontal=True)
    params = default_depth_params(effective_scale(w))
    g = DepthEstimator(K, B, params).disparity(left, right)
    c = DepthEstimator(K, B, DepthParams(**{**params.__dict__, "backend": "cpu"})).disparity(left, right)
    ov = overlap_mask(d_true)
    both = ov & np.isfinite(g) & np.isfinite(c)
    assert both.mean() > 0.8 * ov.mean()
    assert np.median(np.abs(g - c)[both]) < 0.15  # each is ~0.05-0.1 px from the truth
    eg, ec = np.abs(g - d_true)[ov & np.isfinite(g)], np.abs(c - d_true)[ov & np.isfinite(c)]
    assert np.median(eg) < 0.1 and np.median(eg) <= np.median(ec) + 0.02
    assert np.mean(eg > 1.0) <= np.mean(ec > 1.0) + 0.005


@pytest.mark.parametrize("params", [DepthParams(normalize=True), DepthParams(mode="hh4", pad_left=False),
                                    DepthParams(subpixel_refine=False), DepthParams(speckle_window_size=0),
                                    DepthParams(disp12_max_diff=0, uniqueness_ratio=0)])
def test_gpu_options(gpu, params):
    rng = np.random.default_rng(31)
    left, right, z_true, d_true, K = make_pair(0.5, lambda u, v: np.full_like(np.asarray(u, float), 1 / 270.0), rng,
                                               horizontal=True)
    est = DepthEstimator(K, B, params)
    assert est.backend == "gpu"
    depth = est.compute(left, right)
    check(depth, z_true, d_true, max_med_rel=0.006 if not params.subpixel_refine else 0.005)
    if not params.pad_left:  # SGBM's left strip stays invalid
        assert not np.any(depth[:, : est.min_disparity + est.num_disparities] > 0)


def test_odd_image_size(gpu):
    left, right, z_true, d_true, K = plane(0.5, 250.0, 7)
    y0, x0 = 13, 0
    crop = (slice(y0, y0 + 333), slice(x0, x0 + 515))
    l, r, zt, dt = left[crop], right[crop], z_true[crop], d_true[crop]
    Kc = K.copy()
    Kc[1, 2] -= y0
    est = DepthEstimator(Kc, B)
    depth = est.compute(np.ascontiguousarray(l), np.ascontiguousarray(r))
    assert depth.shape == l.shape and est.backend == "gpu"
    check(depth, zt, dt)


def test_tiny_images_go_to_the_cpu(gpu, caplog):
    left, right, z_true, d_true, K = plane(0.5, 250.0, 8)
    est = DepthEstimator(K, B)
    assert est.backend == "gpu"
    with caplog.at_level(logging.WARNING, logger="ohs.vision.depth"):
        depth = est.compute(np.ascontiguousarray(left[:24, :200]), np.ascontiguousarray(right[:24, :200]))
    assert depth.shape == (24, 200) and est.backend == "cpu" and not caplog.records


def test_no_depth_carried_into_dot_free_areas(gpu):
    """Below the projector's field the path costs would carry the dots' depth into the blank part of
    the surface (smooth, plausible, wrong and fixed to the camera); the texture support drops it."""
    rng = np.random.default_rng(9)
    w = 640
    inv_z = lambda u, v: 1 / 230.0 + (1 / 330.0 - 1 / 230.0) * np.asarray(v, float) / 400 + 0 * np.asarray(u, float)  # noqa: E731
    left, right, z_true, d_true, K = make_pair(0.5, inv_z, rng, vertical=True)
    h = left.shape[0]
    cut = 2 * h // 3
    blank = lambda img: np.where(np.arange(h)[:, None] >= cut,  # noqa: E731
                                 np.clip(np.round(18 + rng.normal(0, 2.0, img.shape)), 0, 255), img).astype(np.uint8)
    l2, r2 = blank(left), blank(right)
    # the vertical laser line stays in the blank part (it does on the real scanner)
    line = np.abs(np.arange(w)[None, :] - (0.55 * w + 0.1 * (np.arange(h)[:, None] - h / 2))) < 2
    l2 = np.where(line & (np.arange(h)[:, None] >= cut), left, l2)
    depth = DepthEstimator(K, B, default_depth_params(effective_scale(w))).compute(l2, r2)
    far = np.arange(h)[:, None] >= cut + 10
    away = far & ~(np.abs(np.arange(w)[None, :] - (0.55 * w + 0.1 * (np.arange(h)[:, None] - h / 2))) < 12)
    assert (depth[away] > 0).mean() < 0.01
    ov = overlap_mask(d_true) & (np.arange(h)[:, None] < cut - 10)
    assert (depth[ov] > 0).mean() > 0.85  # the dotted part keeps its depth


def test_no_depth_without_texture_or_correspondence(gpu):
    left, right, z_true, d_true, K = plane(0.5, 200.0, 41)
    est = DepthEstimator(K, B, DepthParams(backend="gpu"))
    depth = est.compute(left, right)
    strip = np.arange(left.shape[1])[None, :] < d_true - 1
    assert (depth[strip] > 0).mean() < 0.005
    flat = np.full_like(left, 30)
    assert not np.isfinite(est.disparity(flat, flat)).any()
    assert (est.compute(flat, flat) > 0).mean() == 0.0


# --------------------------------------------------------------------------- simulated frames
@pytest.fixture(scope="module")
def sim_frames(tmp_path_factory):
    """Three realistic 640x400 frames of the sphere scene, rectified, with the true depth."""
    from ohs.io.recording import RecordingReader
    from recordings import scan_recording
    from ohs.vision.rectify import Rectifier

    rec = scan_recording(tmp_path_factory.mktemp("gpu_depth") / "rec", "sphere_on_plane", n_frames=3,
                         image_size=(640, 400), realistic=True, depth_every=1, seed=5)
    rd = RecordingReader(rec)
    rect = Rectifier(rd.calibration().stereo, 1.0)
    out = []
    for i in range(len(rd)):
        fr = rd.frame(i)
        left, right = rect.rectify(fr.left, fr.right)
        out.append((left, right, rd.ground_truth_depth(int(rd.indices[i]))))
    return out, rect.K, rect.baseline


def _errors(depth, gt, left, K, baseline):
    """Error statistics where the true depth is in range and has a correspondence. ``valid_lit``
    counts only pixels with dots nearby: outside the projector's field the CPU still reports
    (mostly wrong) depth and the GPU deliberately does not."""
    f = float(K[0, 0])
    h, w = gt.shape
    m = (gt >= 150) & (gt <= 450) & ((np.arange(w)[None, :] - f * baseline / np.maximum(gt, 1e-6)) >= 4)
    m[:4], m[-4:], m[:, :4], m[:, -4:] = False, False, False, False
    lit = (cv2.dilate(left, np.ones((7, 7), np.uint8)).astype(int) - cv2.medianBlur(left, 7).astype(int)) > 25
    sel = m & (depth > 0)
    e = np.abs(depth - gt)[sel]
    return {"valid": float(sel.sum() / m.sum()), "valid_lit": float((sel & lit).sum() / (m & lit).sum()),
            "median": float(np.median(e)), "p95": float(np.percentile(e, 95)), "outliers": float(np.mean(e > 2.0)),
            "bias": float(np.median((depth - gt)[sel]))}


def test_gpu_against_cpu_against_ground_truth(gpu, sim_frames):
    frames, K, baseline = sim_frames
    params = default_depth_params(effective_scale(frames[0][0].shape[1]))
    g_est = DepthEstimator(K, baseline, params)
    c_est = DepthEstimator(K, baseline, DepthParams(**{**params.__dict__, "backend": "cpu"}))
    assert g_est.backend == "gpu" and c_est.backend == "cpu"
    g = [_errors(g_est.compute(l, r), gt, l, K, baseline) for l, r, gt in frames]
    c = [_errors(c_est.compute(l, r), gt, l, K, baseline) for l, r, gt in frames]
    mean = lambda rows, k: float(np.mean([x[k] for x in rows]))  # noqa: E731
    print("GPU", {k: round(mean(g, k), 4) for k in g[0]}, "CPU", {k: round(mean(c, k), 4) for k in c[0]})
    # measured (3 frames): median 0.44 vs 0.53 mm, p95 ~3.4 mm both, outliers 11 % vs 13 %
    assert mean(g, "median") <= 1.05 * mean(c, "median") + 0.01
    assert mean(g, "p95") <= 1.1 * mean(c, "p95") + 0.05
    assert mean(g, "outliers") <= mean(c, "outliers") + 0.005
    assert mean(g, "valid_lit") >= mean(c, "valid_lit") - 0.04
    assert abs(mean(g, "bias")) < 0.1 and mean(g, "median") < 0.6


def test_depth_sigma_is_calibrated(sim_frames):
    """compute_with_sigma: the depth of compute(), a sigma exactly where there is depth, and 2 sigma
    covering about 95 % of the true error on both backends (the GPU part needs a GPU)."""
    frames, K, baseline = sim_frames
    params = default_depth_params(effective_scale(frames[0][0].shape[1]))
    backends = ["cpu"] + (["gpu"] if compute.get_device() is not None else [])
    for be in backends:
        est = DepthEstimator(K, baseline, DepthParams(**{**params.__dict__, "backend": be}))
        assert est.backend == be
        inside = n = 0
        for left, right, gt in frames:
            d, s = est.compute_with_sigma(left, right)
            assert np.array_equal(d, est.compute(left, right))
            assert np.array_equal(s > 0, d > 0) and np.all(np.isfinite(s))
            m = (d > 0) & (gt > 0)
            inside += int(np.sum(np.abs(d[m] - gt[m]) < 2 * s[m]))
            n += int(m.sum())
        print(be, "2-sigma coverage", round(inside / n, 4))
        assert 0.9 < inside / n < 0.995, be  # calibrated on other simulated frames: 96-97 % (VALIDATION.md)


# --------------------------------------------------------------------------- the OpenCV twins
def _matcher(gpu, w=320, h=200, params=None):
    from ohs.vision.depth_gpu import GpuStereoMatcher

    p = params or DepthParams()
    mind, nd = disparity_range(360.0, B, p.min_depth_mm, p.max_depth_mm, p.disparity_margin)
    return GpuStereoMatcher(gpu, np.diag([360.0, 360.0, 1.0]), B, p, mind, nd), mind


def _speckled(rng, h, w, invalid):
    """A disparity map (1/16 px) of smooth patches, steps, noise speckles and invalid holes."""
    base = (np.add.outer(np.linspace(40, 90, h), np.linspace(0, 30, w)) * 16).astype(np.int32)
    base[:, w // 2:] += 16 * 12  # a depth step
    noise = rng.integers(-40, 41, (h, w))
    blobs = rng.random((h, w)) < 0.08
    d = np.where(blobs, base + noise * 8, base)
    holes = cv2.dilate((rng.random((h, w)) < 0.01).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    d = np.where(holes, invalid, d)
    return d.astype(np.int16)


def test_speckle_filter_equals_opencv(gpu):
    rng = np.random.default_rng(3)
    for size, rng_px in ((100, 2), (20, 1), (400, 3)):
        p = DepthParams(speckle_window_size=size, speckle_range=rng_px)
        m, mind = _matcher(gpu, params=p)
        invalid = (mind - 1) * 16
        d = _speckled(rng, 200, 320, invalid)
        ref = d.copy()
        cv2.filterSpeckles(ref, invalid, size, 16 * rng_px)
        got = m.filter_disparity(d, median=False)
        assert np.array_equal(got, ref), (size, int((got != ref).sum()))
        assert (ref == invalid).sum() > (d == invalid).sum()  # something was removed


def test_median_equals_opencv(gpu):
    rng = np.random.default_rng(4)
    m, mind = _matcher(gpu, params=DepthParams(speckle_window_size=0))
    d = rng.integers(mind * 16 - 40, (mind + 40) * 16, (200, 320)).astype(np.int16)
    assert np.array_equal(m.filter_disparity(d, median=True), cv2.medianBlur(d, 3))
