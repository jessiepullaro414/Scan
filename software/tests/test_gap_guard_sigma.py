"""The gap guard by the matcher's own doubt (ENGINE_PLAN X1; ``ohs.track.recon_tsdf``, "The gap guard").

* The rule: a pixel with depth within the sigma guard's reach of a patch without depth is dropped
  when its matcher sigma is above the limit; nearer than the distance guard's radius everything is
  dropped as before; beyond the reach nothing is, whatever its sigma. Off (the default), the guard
  is today's, pixel for pixel.
* Without the angle term: the matcher's factor for a steep view is taken out of the sigma first,
  so a steep surface next to a gap is not taken for fill.
* The GPU marks the same pixels as numpy, and counts them as without depth for the sigma's count.
* Fused: a fill that the distance guard alone leaves in the mesh is kept out of it; the 0.5 mm-thin
  edge of surface that is real (a low sigma) is kept.
* The pipeline hands the settings to the confidence TSDF; the Open3D reconstruction (the no-GPU
  path) has no sigma and ignores them, and its depth is exactly as without them.
"""
import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import compute  # noqa: E402
from ohs.geometry import make_se3  # noqa: E402
from ohs.track.recon_tsdf import (SIGMA_GUARD_PX_1280, ConfidenceTsdf, gap_guard_mask,  # noqa: E402
                                  sigma_without_incidence)

W, H, FX = 320, 200, 180.0
K = np.array([[FX, 0.0, (W - 1) / 2], [0.0, FX, (H - 1) / 2], [0.0, 0.0, 1.0]])
Z0 = 300.0


def plane_depth(T_wc: np.ndarray, noise: float = 0.0, rng=None) -> np.ndarray:
    vs, us = np.mgrid[0:H, 0:W].astype(np.float64)
    r = np.stack([(us - K[0, 2]) / FX, (vs - K[1, 2]) / FX, np.ones_like(us)], axis=-1)
    d = r @ T_wc[:3, :3].T
    lam = (Z0 - T_wc[2, 3]) / d[..., 2]
    if noise:
        lam = lam + rng.normal(0.0, noise, lam.shape)
    return lam.astype(np.float32)


def volume(backend: str, **kw) -> ConfidenceTsdf:
    return ConfidenceTsdf(K, (W, H), voxel_mm=2.0, trunc_mm=8.0, depth_min_mm=150.0, depth_max_mm=450.0, backend=backend,
                          **kw)


def holed_image(rng) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A flat depth map with a hole, single missing pixels, a ring of high sigma round the hole (the
    matcher's fill) and a patch of high sigma far from any gap; and the Euclidean distance (px) of
    every pixel to the hole's patch."""
    from scipy.ndimage import convolve, distance_transform_edt

    d = np.full((H, W), 300.0, np.float32)
    d[rng.random(d.shape) < 0.03] = 0.0  # single gaps between dots: no patch
    d[70:110, 120:170] = 0.0  # the hole
    gap = ~(d > 0)
    seed = gap & (convolve(gap.astype(np.int32), np.ones((5, 5), np.int32), mode="nearest") >= 13)
    dist = distance_transform_edt(~seed)
    s = np.full((H, W), 0.4, np.float32)
    s[(dist > 0) & (dist <= 20.0)] = 40.0  # fill-like doubt round the hole, out to 20 px
    s[5:25, 10:40] = 40.0  # doubt far from any patch: never dropped
    s[d <= 0] = 0.0
    return d, s, dist


def test_the_sigma_guard_drops_doubtful_depth_near_a_patch_only():
    d, s, dist = holed_image(np.random.default_rng(3))
    has = d > 0
    base = gap_guard_mask(d, 3.0)
    assert np.array_equal(base, has & (dist <= 3.0))
    # off by default: the sigma alone changes nothing, nor a limit without a reach or the other way round
    assert np.array_equal(gap_guard_mask(d, 3.0, sigma=s), base)
    assert np.array_equal(gap_guard_mask(d, 3.0, sigma=s, sigma_mm=3.0, sigma_px=0.0), base)
    assert np.array_equal(gap_guard_mask(d, 3.0, sigma=s, sigma_mm=None, sigma_px=12.0), base)
    for r_s in (6.0, 12.0, 16.0):
        m = gap_guard_mask(d, 3.0, sigma=s, sigma_mm=3.0, sigma_px=r_s)
        want = has & ((dist <= 3.0) | ((dist <= r_s) & (s > 3.0)))
        assert np.array_equal(m, want), r_s
        assert not m[5:25, 10:40].any()  # far from the hole: kept, whatever its sigma
    # within the reach, depth with a low sigma is kept: real surface next to an edge
    m = gap_guard_mask(d, 3.0, sigma=np.where(has, np.float32(0.4), np.float32(0.0)), sigma_mm=3.0, sigma_px=16.0)
    assert np.array_equal(m, base)
    # instead of the distance guard: the radius 0 with the sigma guard on
    m = gap_guard_mask(d, 0.0, sigma=s, sigma_mm=3.0, sigma_px=12.0)
    assert np.array_equal(m, has & (dist <= 12.0) & (s > 3.0))
    # a sigma that is not a number is no doubt; an infinite one is
    s2 = s.copy()
    s2[90, 175] = np.nan
    s2[90, 176] = np.inf
    m = gap_guard_mask(d, 0.0, sigma=s2, sigma_mm=3.0, sigma_px=12.0)
    assert dist[90, 175] <= 12.0 and not m[90, 175] and m[90, 176] == bool(has[90, 176])


def test_the_sigma_guard_without_the_angle_term():
    """The matcher multiplies a steep view's sigma by ``1 + 60 exp(-(cos / 0.25)^2)``; without the
    angle term that factor is taken out before the sigma is compared with the limit."""
    cos = np.array([1.0, 0.6, 0.3, 0.1], np.float32)
    s = np.full(4, 2.0, np.float32) * (1.0 + 60.0 * np.exp(-(cos / 0.25) ** 2))
    assert np.allclose(sigma_without_incidence(s, cos), 2.0, rtol=1e-5)
    assert s[2] > 3.0 and s[0] < 3.0
    # a tilted plane, steep enough for the factor, with a hole: the guard with and without the term
    from scipy.spatial.transform import Rotation

    T = make_se3(Rotation.from_euler("y", 75.0, degrees=True).as_matrix(), np.array([0.0, 0.0, 220.0]))
    d = plane_depth(T)  # in the fused range from column 109 to 174, at cosines of 0.2 to 0.5
    d[~((d >= 150.0) & (d <= 450.0))] = 0.0
    d[80:120, 130:150] = 0.0
    v = volume("cpu")
    c = v._cos_map(d)
    s = np.where(d > 0, 1.5 * (1.0 + 60.0 * np.exp(-(c / 0.25) ** 2)), 0.0).astype(np.float32)
    near = (d > 0) & ~gap_guard_mask(d, 2.0) & gap_guard_mask(d, 12.0)
    assert near.sum() > 200 and np.median(c[near]) < 0.45 and np.median(s[near]) > 3.0
    with_angle = gap_guard_mask(d, 2.0, sigma=s, sigma_mm=3.0, sigma_px=12.0)
    without = gap_guard_mask(d, 2.0, sigma=sigma_without_incidence(s, c), sigma_mm=3.0, sigma_px=12.0)
    assert with_angle[near].mean() > 0.9  # the steep surface would go
    assert not without[near].any()  # without the angle term it stays


def _gpu_marks(vg: ConfidenceTsdf, img: np.ndarray, sig: np.ndarray | None) -> np.ndarray:
    """The squared distance the GPU's integration takes for the sigma's count: with a guard, the
    one with what the guard drops counted as missing (0 at a dropped pixel)."""
    hold = vg._upload_images(np.ascontiguousarray(img), None if sig is None else np.ascontiguousarray(sig))
    vg._queue_gaps(img.shape, has_sigma=sig is not None)
    got = np.empty((H, W), np.float32)
    vg.cl.enqueue_copy(vg.queue, got, vg._img_bufs[7 if vg._guarded else 4], is_blocking=True)
    del hold
    return got


@pytest.mark.parametrize("angle", [True, False])
def test_the_gpu_marks_the_same_pixels(angle):
    if compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    rng = np.random.default_rng(5)
    d, s, _ = holed_image(rng)
    s = (s * (0.5 + rng.random(s.shape))).astype(np.float32)  # sigmas on both sides of the limit
    s[d <= 0] = 0.0
    for r, rs in ((3.0, 12.0), (0.0, 16.0), (8.0, 4.0)):
        vg = volume("gpu", gap_guard_px=r, sigma_guard_mm=3.0, sigma_guard_px=rs, sigma_guard_angle=angle)
        vc = volume("cpu", gap_guard_px=r, sigma_guard_mm=3.0, sigma_guard_px=rs, sigma_guard_angle=angle)
        mask = vc._guard_mask(d, s)
        assert mask.sum() > 500
        got = _gpu_marks(vg, d, s)
        # the sigma's count takes what the guard drops as missing: 0 there
        assert np.array_equal((got == 0) & (d > 0), mask), (r, rs)
        assert np.array_equal(got, vc._gap2(np.where(mask, np.float32(0.0), d))), (r, rs)
        # without a sigma (none was given) only the distance guard acts
        got = _gpu_marks(vg, d, None)
        assert np.array_equal((got == 0) & (d > 0), gap_guard_mask(d, r)), (r, rs)
        vg.close()


def doubtful_hole_frames(rng, n: int = 10) -> list:
    """The plane with a hole of radius 25 mm round world (40, 0), whose first 8 mm (4.8 px) every frame
    fills with depth of a high sigma (the matcher's fill); the plane itself has a low sigma."""
    vs, us = np.mgrid[0:H, 0:W].astype(np.float64)
    out = []
    for k in range(n):
        T = make_se3(np.eye(3), np.array([6.0 * k, 0.0, 0.0]))
        d = plane_depth(T, 0.1, rng)
        ray = np.stack([(us - K[0, 2]) / FX, (vs - K[1, 2]) / FX, np.ones_like(us)], axis=-1) @ T[:3, :3].T
        X = T[:3, 3] + ray * d[..., None]
        r = np.hypot(X[..., 0] - 40.0, X[..., 1])
        d[r < 17.0] = 0.0
        s = np.where(r < 25.0, np.float32(30.0), np.float32(0.3)).astype(np.float32)
        out.append((d, s, T))
    return out


@pytest.mark.parametrize("backend", ["cpu", "gpu"])
def test_the_sigma_guard_keeps_the_fill_out_of_the_mesh(backend):
    """The 8 mm fill reaches 4.8 px past the patch here: the distance guard at 2 px (3.3 mm) leaves
    most of it; the sigma guard (12 px) removes it and keeps the plane outside the hole."""
    if backend == "gpu" and compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    frames = doubtful_hole_frames(np.random.default_rng(9))
    reach = {}
    for name, kw in (("distance", {}), ("sigma", {"sigma_guard_mm": 3.0, "sigma_guard_px": 12.0})):
        v = volume(backend, gap_guard_px=2.0, **kw)
        for d, sg, T in frames:
            v.integrate(d, sg, T)
        V, _, _ = v.extract_mesh()
        reach[name] = np.hypot(V[:, 0] - 40.0, V[:, 1]).min()
        assert len(V) > 5000
        v.close()
    assert reach["distance"] < 22.0  # the fill is in the mesh
    assert 24.0 < reach["sigma"] < 29.0  # no fill (the mesh's edge within half a voxel), and not much of the plane lost


@pytest.mark.parametrize("backend", ["cpu", "gpu"])
def test_off_by_default_and_the_same_as_before(backend):
    """Without the settings (the default) a volume fuses exactly as one with the sigma guard asked
    for with no limit; its reach default is 16 px at 1280 px width."""
    if backend == "gpu" and compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    frames = doubtful_hole_frames(np.random.default_rng(2), n=4)
    a, b = volume(backend), volume(backend, sigma_guard_mm=None, sigma_guard_px=12.0)
    assert a.sigma_guard_mm is None and a.sigma_guard_px == pytest.approx(SIGMA_GUARD_PX_1280 * W / 1280.0)
    for d, s, T in frames:
        a.integrate(d, s, T)
        b.integrate(d, s, T)
    (ka, ca), (kb, cb) = a.download(), b.download()
    assert np.array_equal(ka, kb) and all(np.array_equal(ca[c], cb[c]) for c in ca)
    with pytest.raises(ValueError):
        volume(backend, sigma_guard_mm=-1.0)
    with pytest.raises(ValueError):
        volume(backend, sigma_guard_mm=3.0, sigma_guard_px=-2.0)
    a.close()
    b.close()


def test_the_pipeline_hands_the_settings_to_the_confidence_tsdf_only():
    from ohs.app.pipeline import FramePipeline, PipelineParams
    from ohs.sim.nominal import nominal_calibration

    calib = nominal_calibration()
    p = PipelineParams(scale=0.5, use_lasers=False, gap_guard_sigma_mm=3.0, gap_guard_sigma_px=6.0,
                       gap_guard_sigma_angle=False)
    if compute.get_device() is not None:
        with FramePipeline(calib, p) as pipe:
            m = pipe.markerless
            assert isinstance(m, ConfidenceTsdf)
            assert (m.sigma_guard_mm, m.sigma_guard_px, m.sigma_guard_angle) == (3.0, 6.0, False)
        with FramePipeline(calib, PipelineParams(scale=0.5, use_lasers=False, gap_guard_sigma_mm=3.0)) as pipe:
            assert pipe.markerless.sigma_guard_px == pytest.approx(SIGMA_GUARD_PX_1280 * pipe.image_size[0] / 1280.0)
        with FramePipeline(calib, PipelineParams(scale=0.5, use_lasers=False)) as pipe:
            assert pipe.markerless.sigma_guard_mm is None  # off by default
    # the Open3D reconstruction (the no-GPU path) has no sigma: the settings change nothing there
    got = {}
    for name, prm in (("off", PipelineParams(scale=0.5, use_lasers=False, recon="open3d")),
                      ("on", PipelineParams(scale=0.5, use_lasers=False, recon="open3d", gap_guard_sigma_mm=3.0,
                                            gap_guard_sigma_px=12.0))):
        with FramePipeline(calib, prm) as pipe:
            w, h = pipe.image_size
            d = np.full((h, w), 300.0, np.float32)
            d[h // 2 - 20:h // 2 + 20, w // 2 - 20:w // 2 + 20] = 0.0
            s = np.full((h, w), 40.0, np.float32)
            seen = {}
            pipe.markerless.integrate = lambda depth, sigma, T, t=None: seen.update(depth=np.array(depth))
            pipe._fuse(d.copy(), np.eye(4), 0.0, {}, s)
            got[name] = seen["depth"]
    assert np.array_equal(got["off"], got["on"]) and (got["on"] == 0).sum() > (d == 0).sum()
    for bad in ({"gap_guard_sigma_mm": -1.0}, {"gap_guard_sigma_px": -1.0}, {"gap_guard_sigma_mm": float("nan")}):
        with pytest.raises(ValueError):
            PipelineParams(**bad)
