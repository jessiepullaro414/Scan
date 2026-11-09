"""Tests for ohs.vision.rectify: known 3D points through a non-ideal (distorted, rotated) rig."""
import cv2
import numpy as np
import pytest
from scipy.special import erf

from ohs import geometry as G
from ohs.types import StereoCalibration
from ohs.vision.rectify import Rectifier

SIZE = (1280, 800)


def nonideal_calibration() -> StereoCalibration:
    K1 = np.array([[722.0, 0.0, 645.3], [0.0, 719.0, 396.8], [0.0, 0.0, 1.0]])
    K2 = np.array([[718.0, 0.0, 634.1], [0.0, 716.5, 403.2], [0.0, 0.0, 1.0]])
    D1 = np.array([-0.12, 0.05, 0.0008, -0.0005, -0.01])
    D2 = np.array([-0.10, 0.03, -0.0006, 0.0004, 0.0])
    R = G.so3_exp(np.array([0.012, -0.02, 0.008]))  # ~1.4 deg relative rotation
    T = np.array([-60.2, 0.4, -0.8])
    return StereoCalibration.build(SIZE, K1, D1, K2, D2, R, T)


def render_blobs(uv: np.ndarray, size=SIZE, sigma=1.3, amp=200.0) -> np.ndarray:
    """Pixel-area-integrated Gaussian blobs at sub-pixel positions ``uv`` (N,2)."""
    w, h = size
    img = np.zeros((h, w), np.float64)
    r = int(np.ceil(4 * sigma))
    s2 = np.sqrt(2.0) * sigma
    for u, v in uv:
        x = np.arange(int(u) - r, int(u) + r + 2)
        y = np.arange(int(v) - r, int(v) + r + 2)
        x, y = x[(x >= 0) & (x < w)], y[(y >= 0) & (y < h)]
        gx = 0.5 * (erf((x + 0.5 - u) / s2) - erf((x - 0.5 - u) / s2))
        gy = 0.5 * (erf((y + 0.5 - v) / s2) - erf((y - 0.5 - v) / s2))
        img[np.ix_(y, x)] += amp * 2 * np.pi * sigma**2 * np.outer(gy, gx)
    return np.clip(np.round(img), 0, 255).astype(np.uint8)


def centroids(img: np.ndarray, uv_pred: np.ndarray, r: int = 5) -> np.ndarray:
    out = np.full((len(uv_pred), 2), np.nan)
    f = img.astype(np.float64)
    for i, (u, v) in enumerate(uv_pred):
        u0, v0 = int(round(u)), int(round(v))
        patch = f[v0 - r : v0 + r + 1, u0 - r : u0 + r + 1]
        ys, xs = np.mgrid[v0 - r : v0 + r + 1, u0 - r : u0 + r + 1]
        m = patch.sum()
        if m > 0:
            out[i] = [(patch * xs).sum() / m, (patch * ys).sum() / m]
    return out


def scene(calib: StereoCalibration):
    """3D points (frame C) on a grid covering the image, at 180..420 mm, plus raw pixel positions."""
    K = calib.K_rect
    us, vs = np.meshgrid(np.linspace(330, 1220, 8), np.linspace(40, 760, 7))
    uv_rect = np.stack([us.ravel(), vs.ravel()], 1)
    z = np.linspace(180, 420, len(uv_rect))
    np.random.default_rng(0).shuffle(z)
    X_c = G.pixels_to_rays(uv_rect, K) * z[:, None]
    X_l = X_c @ calib.R1  # raw left camera frame: X_l = R1^T X_c
    raw_l, _ = cv2.projectPoints(X_l, np.zeros(3), np.zeros(3), calib.K1, calib.D1)
    rvec, _ = cv2.Rodrigues(calib.R)
    raw_r, _ = cv2.projectPoints(X_l, rvec, calib.T, calib.K2, calib.D2)
    return X_c, raw_l.reshape(-1, 2), raw_r.reshape(-1, 2)


def project_rect(P: np.ndarray, X_c: np.ndarray) -> np.ndarray:
    x = np.hstack([X_c, np.ones((len(X_c), 1))]) @ P.T
    return x[:, :2] / x[:, 2:3]


@pytest.fixture(scope="module")
def raw_pair():
    calib = nonideal_calibration()
    X_c, raw_l, raw_r = scene(calib)
    inside = lambda p: np.all((p > 8) & (p < np.array(SIZE) - 8), axis=1)  # noqa: E731
    keep = inside(raw_l) & inside(raw_r)
    assert keep.sum() >= 50
    X_c, raw_l, raw_r = X_c[keep], raw_l[keep], raw_r[keep]
    return calib, X_c, render_blobs(raw_l), render_blobs(raw_r)


@pytest.mark.parametrize("scale", [1.0, 0.5])
def test_rectified_rows_and_disparity(raw_pair, scale):
    calib, X_c, left, right = raw_pair
    rect = Rectifier(calib, scale=scale)
    left_r, right_r = rect.rectify(left, right)
    w, h = rect.image_size
    assert left_r.shape == (h, w) and right_r.shape == (h, w) and left_r.dtype == np.uint8
    assert (w, h) == (round(SIZE[0] * scale), round(SIZE[1] * scale))

    K, B = rect.K, rect.baseline
    assert B == pytest.approx(np.linalg.norm(calib.T), rel=1e-6)
    assert K[0, 0] == pytest.approx(calib.P1[0, 0] * scale)
    pl = project_rect(rect.calib.P1, X_c)
    pr = project_rect(rect.calib.P2, X_c)
    cl = centroids(left_r, pl, r=max(3, int(5 * scale)))
    cr = centroids(right_r, pr, r=max(3, int(5 * scale)))
    assert np.all(np.isfinite(cl)) and np.all(np.isfinite(cr))

    dv = cl[:, 1] - cr[:, 1]
    disp = cl[:, 0] - cr[:, 0]
    disp_pred = K[0, 0] * B / X_c[:, 2]
    assert np.max(np.abs(dv)) < 0.1, f"row mismatch {np.max(np.abs(dv)):.3f} px"
    assert np.max(np.abs(disp - disp_pred)) < 0.1, f"disparity error {np.max(np.abs(disp - disp_pred)):.3f} px"
    # positions agree with the rectified projection P1 (frame C) itself
    assert np.max(np.abs(cl - pl)) < 0.1 and np.max(np.abs(cr - pr)) < 0.1


def test_rectify_left_matches_pair_and_properties(raw_pair):
    calib, _, left, right = raw_pair
    rect = Rectifier(calib, scale=0.5)
    l1, _ = rect.rectify(left, right)
    assert np.array_equal(l1, rect.rectify_left(left))
    assert rect.calib.image_size == (640, 400)
    assert np.allclose(rect.K, calib.scaled(0.5).K_rect)
    assert rect.raw_image_size == SIZE and rect.scale == 0.5
    # colour input is remapped per channel
    col = np.dstack([left, left, left])
    assert np.array_equal(rect.rectify_left(col)[..., 1], l1)


def test_rectify_rejects_wrong_size(raw_pair):
    calib, _, left, _ = raw_pair
    rect = Rectifier(calib)
    with pytest.raises(ValueError):
        rect.rectify_left(left[::2, ::2])
    with pytest.raises(ValueError):
        Rectifier(calib, scale=0.0)


def test_identity_rig_is_a_pure_resample():
    """With an ideal rig, scale=0.5 rectification equals 2x2 box downsampling (pixel-centre convention)."""
    f = 720.0
    K = np.array([[f, 0, 639.5], [0, f, 399.5], [0, 0, 1.0]])
    calib = StereoCalibration.build(SIZE, K, np.zeros(5), K, np.zeros(5), np.eye(3), np.array([-60.0, 0, 0]))
    rng = np.random.default_rng(3)
    img = rng.integers(0, 256, (SIZE[1], SIZE[0])).astype(np.uint8)
    full = Rectifier(calib).rectify_left(img)
    assert np.array_equal(full, img)
    half = Rectifier(calib, 0.5).rectify_left(img).astype(np.float64)
    box = cv2.resize(img, (640, 400), interpolation=cv2.INTER_AREA).astype(np.float64)
    assert np.abs(half - box)[1:-1, 1:-1].max() <= 1.0
