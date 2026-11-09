"""Tests for ohs.vision.depth on synthetic random-dot stereo pairs.

The left image is rendered analytically (pixel-area-integrated Gaussian dots, optional saturated
laser lines). The right image is the left image remapped by the exact sub-pixel disparity of a
plane (``right(u_r, v) = left(u_r + d, v)``, Lanczos interpolation), with independent sensor noise.
"""
import cv2
import numpy as np
import pytest
from scipy.special import erf

from ohs.vision.depth import DepthEstimator, DepthParams, disparity_range

B = 60.0
RES = {1.0: (1280, 800, 720.0, (0.6, 0.9)), 0.5: (640, 400, 360.0, (0.35, 0.5))}  # w, h, f, dot sigma


def render_dots(w, h, rng, n=10000, sigma=(0.6, 0.9), amp=(80.0, 200.0), bg=18.0) -> np.ndarray:
    img = np.full((h, w), bg)
    u, v = rng.uniform(-3, w + 3, n), rng.uniform(-3, h + 3, n)
    s, a = rng.uniform(*sigma, n), rng.uniform(*amp, n)
    ui, vi, s2 = np.floor(u).astype(int), np.floor(v).astype(int), np.sqrt(2.0) * s
    for dy in range(-3, 4):
        y = vi + dy
        gy = 0.5 * (erf((y + 0.5 - v) / s2) - erf((y - 0.5 - v) / s2))
        for dx in range(-3, 4):
            x = ui + dx
            ok = (x >= 0) & (x < w) & (y >= 0) & (y < h)
            gx = 0.5 * (erf((x + 0.5 - u) / s2) - erf((x - 0.5 - u) / s2))
            np.add.at(img, (y[ok], x[ok]), (a * 2 * np.pi * s * s * gx * gy)[ok])
    return img


def add_lines(img, scale, vertical=True, horizontal=False, peak=600.0):
    """Saturating Gaussian laser lines (sigma 1.5 px at full resolution)."""
    h, w = img.shape
    vv, uu = np.mgrid[0:h, 0:w].astype(np.float64)
    sig = 1.5 * scale
    if vertical:
        img = img + peak * np.exp(-0.5 * ((uu - 0.55 * w - 0.1 * (vv - h / 2)) / sig) ** 2)
    if horizontal:
        img = img + peak * np.exp(-0.5 * ((vv - 0.4 * h - 0.05 * (uu - w / 2)) / sig) ** 2)
    return img


def make_pair(scale, inv_z, rng, vertical=True, horizontal=False, gain_r=1.0, noise=2.0):
    """``inv_z(u, v)`` is 1/z (affine in u, v for a plane) on the LEFT image grid.

    Returns ``(left8, right8, z_true, d_true, K)``.
    """
    w, h, f, sig = RES[scale]
    K = np.array([[f, 0, w / 2 - 0.5], [0, f, h / 2 - 0.5], [0, 0, 1.0]])
    left = add_lines(render_dots(w, h, rng, sigma=sig), scale, vertical, horizontal)
    vv, uu = np.mgrid[0:h, 0:w].astype(np.float64)
    # plane: d(u_l, v) = f B (a u_l + b v + c)  ->  u_l = (u_r + f B (b v + c)) / (1 - f B a)
    c = inv_z(0.0, 0.0)
    a = inv_z(1.0, 0.0) - c
    b = inv_z(0.0, 1.0) - c
    fb = f * B
    ul = (uu + fb * (b * vv + c)) / (1.0 - fb * a)
    right = cv2.remap(left.astype(np.float32), ul.astype(np.float32), vv.astype(np.float32), cv2.INTER_LANCZOS4,
                      borderMode=cv2.BORDER_CONSTANT, borderValue=18.0)
    z_true = 1.0 / inv_z(uu, vv)
    to8 = lambda x: np.clip(np.round(x + rng.normal(0, noise, x.shape)), 0, 255).astype(np.uint8)  # noqa: E731
    return to8(left), to8(gain_r * right.astype(np.float64)), z_true, fb / z_true, K


def overlap_mask(d_true, margin=6):
    h, w = d_true.shape
    m = np.zeros((h, w), bool)
    m[margin:h - margin, margin:w - margin] = True
    return m & ((np.arange(w)[None, :] - d_true) >= margin)


def check(depth, z_true, d_true, max_med_rel=0.005, min_valid=0.8):
    ov = overlap_mask(d_true)
    valid = depth > 0
    rel = np.abs(depth - z_true)[valid & ov] / z_true[valid & ov]
    frac = valid[ov].mean()
    med = float(np.median(rel))
    assert frac > min_valid, f"valid fraction {frac:.3f}"
    assert med < max_med_rel, f"median relative error {med * 100:.3f}%"
    assert np.mean(rel > 0.02) < 0.01, f"gross errors {np.mean(rel > 0.02):.4f}"
    return frac, med


# --------------------------------------------------------------------------- range derivation
def test_disparity_range_from_depth_limits():
    assert disparity_range(720.0, 60.0, 150.0, 450.0) == (83, 208)
    assert disparity_range(360.0, 60.0, 150.0, 450.0) == (35, 112)
    rng = np.random.default_rng(0)
    for _ in range(200):
        f, b = rng.uniform(200, 1500), rng.uniform(20, 120)
        zmin = rng.uniform(80, 300)
        zmax = zmin * rng.uniform(1.2, 5.0)
        lo, n = disparity_range(f, b, zmin, zmax)
        assert n % 16 == 0 and n >= 16 and lo >= 0
        assert lo <= f * b / zmax - 2 + 1e-9 or lo == 0
        assert lo + n - 1 >= f * b / zmin + 2
        assert n - 16 < f * b / zmin - f * b / zmax + 8  # no more than one extra block of 16
    est = DepthEstimator(np.diag([720.0, 720.0, 1.0]), 60.0)
    assert (est.min_disparity, est.num_disparities) == (83, 208)


# --------------------------------------------------------------------------- accuracy
@pytest.mark.parametrize("scale", [0.5, 1.0])
@pytest.mark.parametrize("z", [200.0, 250.0, 400.0])
def test_fronto_parallel_planes(scale, z):
    rng = np.random.default_rng(int(z) + int(scale * 10))
    left, right, z_true, d_true, K = make_pair(scale, lambda u, v: np.full_like(np.asarray(u, float), 1.0 / z), rng)
    depth = DepthEstimator(K, B, DepthParams()).compute(left, right)
    assert depth.dtype == np.float32 and depth.shape == left.shape
    check(depth, z_true, d_true)


@pytest.mark.parametrize("scale", [0.5, 1.0])
def test_slanted_plane_with_saturated_laser_lines(scale):
    w = RES[scale][0]
    rng = np.random.default_rng(11)
    inv_z = lambda u, v: 1 / 220.0 + (1 / 380.0 - 1 / 220.0) * np.asarray(u, float) / w + 2e-7 * np.asarray(v, float)  # noqa: E731
    left, right, z_true, d_true, K = make_pair(scale, inv_z, rng, vertical=True, horizontal=True)
    assert (left == 255).sum() > 0.5 * left.shape[0]  # the lines really are saturated
    depth = DepthEstimator(K, B, DepthParams()).compute(left, right)
    check(depth, z_true, d_true)
    # matching is not broken around the lines either
    vv, uu = np.mgrid[0:left.shape[0], 0:left.shape[1]]
    near_v = np.abs(uu - 0.55 * w - 0.1 * (vv - left.shape[0] / 2)) < 6 * scale + 2
    ov = overlap_mask(d_true) & near_v
    good = (depth > 0) & (np.abs(depth - z_true) < 0.01 * z_true)
    assert good[ov].mean() > 0.8


def test_subpixel_refinement_reduces_pixel_locking():
    rng = np.random.default_rng(5)
    w = RES[0.5][0]
    inv_z = lambda u, v: 1 / 220.0 + (1 / 380.0 - 1 / 220.0) * np.asarray(u, float) / w + 0 * np.asarray(v, float)  # noqa: E731
    left, right, z_true, d_true, K = make_pair(0.5, inv_z, rng)
    ov = overlap_mask(d_true)
    errs = {}
    for refine in (False, True):
        disp = DepthEstimator(K, B, DepthParams(subpixel_refine=refine)).disparity(left, right)
        e = np.abs(disp - d_true)[ov & np.isfinite(disp)]
        errs[refine] = np.median(e)
    assert errs[True] < 0.8 * errs[False], errs
    assert errs[True] < 0.1


def test_gain_mismatch_between_cameras():
    rng = np.random.default_rng(21)
    w = RES[0.5][0]
    inv_z = lambda u, v: 1 / 230.0 + (1 / 350.0 - 1 / 230.0) * np.asarray(u, float) / w + 0 * np.asarray(v, float)  # noqa: E731
    left, right, z_true, d_true, K = make_pair(0.5, inv_z, rng, gain_r=0.75)
    check(DepthEstimator(K, B, DepthParams()).compute(left, right), z_true, d_true)


@pytest.mark.parametrize("params", [DepthParams(wls=True), DepthParams(normalize=True), DepthParams(mode="hh4", pad_left=False)])
def test_optional_modes(params):
    rng = np.random.default_rng(31)
    left, right, z_true, d_true, K = make_pair(0.5, lambda u, v: np.full_like(np.asarray(u, float), 1 / 270.0), rng, horizontal=True)
    check(DepthEstimator(K, B, params).compute(left, right), z_true, d_true)


# --------------------------------------------------------------------------- invalid regions
def test_no_depth_without_correspondence():
    rng = np.random.default_rng(41)
    left, right, z_true, d_true, K = make_pair(0.5, lambda u, v: np.full_like(np.asarray(u, float), 1 / 200.0), rng)
    depth = DepthEstimator(K, B, DepthParams()).compute(left, right)
    strip = np.arange(left.shape[1])[None, :] < d_true - 1  # match would lie left of the right image
    assert (depth[strip] > 0).mean() < 0.005
    # a surface beyond max_depth must not be reported inside the range
    left, right, *_ = make_pair(0.5, lambda u, v: np.full_like(np.asarray(u, float), 1 / 600.0), rng)
    depth = DepthEstimator(K, B, DepthParams()).compute(left, right)
    assert (depth > 0).mean() < 0.01
    # a textureless pair gives (almost) nothing
    flat = np.full_like(left, 30)
    assert (DepthEstimator(K, B).compute(flat, flat) > 0).mean() < 0.01


def test_api_contract():
    rng = np.random.default_rng(51)
    left, right, z_true, d_true, K = make_pair(0.5, lambda u, v: np.full_like(np.asarray(u, float), 1 / 300.0), rng)
    est = DepthEstimator(K, B)
    disp = est.disparity(left, right)
    assert disp.dtype == np.float32 and np.isnan(disp).any() and np.nanmin(disp) >= est.min_disparity
    depth = est.depth_from_disparity(disp)
    assert not np.any((depth > 0) & ~np.isfinite(disp))
    assert np.all((depth == 0) | ((depth >= 150) & (depth <= 450)))
    assert np.allclose(depth[depth > 0], est.f * B / disp[depth > 0], rtol=1e-5)
    assert np.array_equal(est.compute(left, right), depth)
    with pytest.raises(ValueError):
        est.compute(left, right[:, :-1])
    with pytest.raises(ValueError):
        est.compute(np.dstack([left] * 3), np.dstack([right] * 3))
    with pytest.raises(ValueError):
        DepthParams(min_depth_mm=300, max_depth_mm=200)
    with pytest.raises(ValueError):
        DepthParams(block_size=4)
