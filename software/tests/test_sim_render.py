"""Simulator renderer: geometric correctness of images and depth (numeric checks)."""
import time

import cv2
import numpy as np
import pytest

from ohs.geometry import make_se3, pixels_to_rays, ray_plane_intersect
from ohs.sim.nominal import nominal_calibration, nominal_emitters
from ohs.sim.render import Renderer, RenderParams
from ohs.sim.scene import ConstantAlbedo, Rect, Scene, charuco_board, sphere_on_plane
from ohs.sim.trajectory import look_at

SPHERE_C = np.array([0.0, 0.0, 40.0])
SPHERE_R = 40.0


def scanner_pose(eye, target, up=(0.0, 0.0, 1.0)):
    """T_wc with the scanner centre (30 mm right of the left camera) at ``eye`` looking at ``target``."""
    T = look_at(eye, target, up=up)
    T[:3, 3] -= 30.0 * T[:3, 0]
    return T


POSE = scanner_pose([40.0, -150.0, 220.0], [0.0, 0.0, 30.0])


def analytic_depth(K, T_wc, size):
    """z-depth (mm) through each rectified-left pixel centre for the sphere-on-plane scene."""
    w, h = size
    U, V = np.meshgrid(np.arange(w), np.arange(h))
    rays = pixels_to_rays(np.column_stack([U.ravel(), V.ravel()]), K)
    R, t = T_wc[:3, :3], T_wc[:3, 3]
    n_c = R.T @ np.array([0.0, 0.0, 1.0])
    with np.errstate(divide="ignore"):
        zp = -t[2] / (rays @ n_c)
    zp[~(zp > 0)] = np.inf
    c = R.T @ (SPHERE_C - t)
    a = (rays**2).sum(1)
    b = -2.0 * rays @ c
    disc = b * b - 4 * a * (c @ c - SPHERE_R**2)
    zs = np.where(disc >= 0, (-b - np.sqrt(np.maximum(disc, 0.0))) / (2 * a), np.inf)
    return np.minimum(zp, zs).reshape(h, w)


def surface_distance(X_w):
    """Distance (mm) of world points to the nearest true surface of sphere_on_plane."""
    return np.minimum(np.abs(X_w[:, 2]), np.abs(np.linalg.norm(X_w - SPHERE_C, axis=1) - SPHERE_R))


def ridge_points(img, vertical, min_peak=100.0):
    """Brightest pixel per row (vertical line) / column (horizontal line), centroid-refined (+-3 px)."""
    I = img.astype(np.float64)
    if not vertical:
        I = I.T
    out = []
    for k, line in enumerate(I):
        j = int(np.argmax(line))
        if line[j] < min_peak or j < 3 or j > len(line) - 4:
            continue
        w = np.clip(line[j - 3 : j + 4] - np.median(line), 0.0, None)
        c = j - 3 + float((w * np.arange(7)).sum() / w.sum())
        out.append((c, k) if vertical else (k, c))
    return np.array(out)


@pytest.mark.parametrize("realistic", [False, True])
def test_depth_matches_analytic_sphere_and_plane(realistic):
    cal = nominal_calibration((640, 400), realistic=realistic)
    with Renderer(cal, None, sphere_on_plane(), RenderParams(seed=1)) as r:
        left, right, depth = r.render(POSE)
    assert left.shape == right.shape == depth.shape == (400, 640) and depth.dtype == np.float32
    za = analytic_depth(cal.stereo.K_rect, POSE, (640, 400))
    valid = np.isfinite(za)
    assert np.array_equal(valid, depth > 0)
    assert np.abs(depth[valid] - za[valid]).max() < 0.01
    assert (np.abs(depth[valid] - za[valid]) < 1e-3).mean() > 0.999


@pytest.mark.slow
@pytest.mark.parametrize("realistic", [False, True])
def test_laser_pixels_triangulate_onto_true_surface(realistic):
    """Brightest pixel per row/column, triangulated with the TRUE plane, lies on the TRUE surface."""
    cal = nominal_calibration((1280, 800), realistic=realistic)
    st = cal.stereo
    K = st.K_rect
    if realistic:
        mx, my = cv2.initUndistortRectifyMap(st.K1, st.D1, st.R1, st.P1, st.image_size, cv2.CV_32FC1)
    for name, vertical in (("vertical", True), ("horizontal", False)):
        params = RenderParams(dots_on=False, line_names=(name,), seed=4)
        with Renderer(cal, None, sphere_on_plane(tags=False), params) as r:
            left, _, _ = r.render(POSE)
        img = cv2.remap(left, mx, my, cv2.INTER_LINEAR) if realistic else left
        uv = ridge_points(img, vertical)
        assert len(uv) > (300 if vertical else 500)
        plane = cal.lasers[name]
        X = ray_plane_intersect(pixels_to_rays(uv, K), plane.normal, plane.d)
        Xw = X @ POSE[:3, :3].T + POSE[:3, 3]
        err = surface_distance(Xw)
        assert np.isfinite(err).all()
        assert err.max() < 0.5, f"{name}: max {err.max():.3f} mm"
        assert np.median(err) < 0.1
        # the line really is on both objects (sphere and table)
        on_sphere = np.abs(np.linalg.norm(Xw - SPHERE_C, axis=1) - SPHERE_R) < 0.5
        assert 20 < on_sphere.sum() < len(Xw) - 20


@pytest.mark.parametrize("realistic", [False, True])
def test_dot_pattern_is_stereo_matchable(realistic):
    """OpenCV SGBM on the rectified pair of a plain plane: median depth error < 1 %."""
    cal = nominal_calibration((640, 400), realistic=realistic)
    st = cal.stereo
    plane = Scene([Rect(make_se3(np.diag([1.0, -1.0, -1.0])), (-600, 600, -600, 600), ConstantAlbedo(0.6))])
    T = scanner_pose([60.0, -90.0, 260.0], [30.0, 0.0, 0.0])
    with Renderer(cal, None, plane, RenderParams(lines_on=False, seed=2)) as r:
        left, right, depth = r.render(T)
    if realistic:
        maps = [cv2.initUndistortRectifyMap(K, D, Rr, P, st.image_size, cv2.CV_32FC1)
                for K, D, Rr, P in ((st.K1, st.D1, st.R1, st.P1), (st.K2, st.D2, st.R2, st.P2))]
        left = cv2.remap(left, *maps[0], cv2.INTER_LINEAR)
        right = cv2.remap(right, *maps[1], cv2.INTER_LINEAR)
    sgbm = cv2.StereoSGBM.create(minDisparity=32, numDisparities=128, blockSize=5, P1=8 * 25, P2=32 * 25,
                                 uniquenessRatio=10, speckleWindowSize=100, speckleRange=2, disp12MaxDiff=1)
    disp = sgbm.compute(left, right).astype(np.float32) / 16.0
    valid = (disp > 32) & (depth > 0)
    z = st.K_rect[0, 0] * st.baseline / np.where(valid, disp, 1.0)
    rel = np.abs(z[valid] - depth[valid]) / depth[valid]
    assert valid.mean() > 0.5
    assert np.median(rel) < 0.01


def test_without_dots_the_plain_plane_is_not_matchable():
    cal = nominal_calibration((640, 400))
    plane = Scene([Rect(make_se3(np.diag([1.0, -1.0, -1.0])), (-600, 600, -600, 600), ConstantAlbedo(0.6))])
    T = scanner_pose([60.0, -90.0, 260.0], [30.0, 0.0, 0.0])
    with Renderer(cal, None, plane, RenderParams(dots_on=False, lines_on=False, seed=2)) as r:
        left, right, _ = r.render(T)
    sgbm = cv2.StereoSGBM.create(minDisparity=32, numDisparities=128, blockSize=5, P1=200, P2=800, uniquenessRatio=10,
                                 speckleWindowSize=100, speckleRange=2, disp12MaxDiff=1)
    disp = sgbm.compute(left, right).astype(np.float32) / 16.0
    assert (disp > 32).mean() < 0.2


def sphere_shadow(Pw, src_w):
    """Analytic: does the segment from table points ``Pw`` to the emitter cross the sphere?"""
    dvec = src_w - Pw
    L = np.linalg.norm(dvec, axis=1)
    u = dvec / L[:, None]
    oc = Pw - SPHERE_C
    b = (oc * u).sum(1)
    disc = b * b - ((oc * oc).sum(1) - SPHERE_R**2)
    t1 = -b - np.sqrt(np.maximum(disc, 0.0))
    return (disc > 0) & (t1 > 0) & (t1 < L)


def test_dot_projector_shadow_behind_sphere():
    cal = nominal_calibration((640, 400))
    params = RenderParams(ambient_on=False, lines_on=False, noise_sigma=0.0, shot_noise=0.0, black_level=0.0, seed=5)
    with Renderer(cal, None, sphere_on_plane(tags=False), params) as r:
        aux = r.render_aux(POSE)
        left, _, _ = r.render(POSE)
    em = nominal_emitters()["dots"]
    on_plane = aux["obj"] == 0
    Pc = aux["points_c"][on_plane]
    v = Pc - em["origin"]
    tx, ty = np.tan(np.radians(40.0)), np.tan(np.radians(32.2))
    in_field = (v[:, 2] > 0) & (np.abs(v[:, 0] / v[:, 2]) < tx * 0.995) & (np.abs(v[:, 1] / v[:, 2]) < ty * 0.995)
    shadow = sphere_shadow(Pc @ POSE[:3, :3].T + POSE[:3, 3], POSE[:3, :3] @ em["origin"] + POSE[:3, 3])
    lit = aux["lit_dots"][on_plane]
    assert shadow[in_field].sum() > 500  # a real, visible shadow behind the sphere
    assert (lit[in_field] == ~shadow[in_field]).mean() > 0.999
    # the image is black in the shadow core and dotted in the lit part of the table
    sh_img = np.zeros(on_plane.shape, np.uint8)
    sh_img[on_plane] = shadow & in_field
    lit_img = np.zeros(on_plane.shape, np.uint8)
    lit_img[on_plane] = ~shadow & in_field
    k = np.ones((5, 5), np.uint8)
    core_shadow = cv2.erode(sh_img, k).astype(bool)
    core_lit = cv2.erode(lit_img, k).astype(bool)
    assert core_shadow.sum() > 100
    assert left[core_shadow].max() <= 1  # no dot light at all
    assert (left[core_lit] > 20).mean() > 0.05  # dots everywhere else


def test_laser_sheet_shadows_behind_sphere():
    cal = nominal_calibration((640, 400))
    em = nominal_emitters()
    with Renderer(cal, None, sphere_on_plane(tags=False), RenderParams(ambient_on=False, dots_on=False, seed=5)) as r:
        aux = r.render_aux(POSE)
    on_plane = aux["obj"] == 0
    Pc = aux["points_c"][on_plane]
    Pw = Pc @ POSE[:3, :3].T + POSE[:3, 3]
    for name in ("vertical", "horizontal"):
        e = em[name]
        w = Pc - e["origin"]
        phi = np.degrees(np.arctan2(w @ e["fan_axis"], w @ e["direction"]))
        band = (np.abs(Pc @ e["normal"] + e["d"]) < 0.4) & (np.abs(phi) < 28.0)
        shadow = sphere_shadow(Pw, POSE[:3, :3] @ e["origin"] + POSE[:3, 3])
        lit = aux[f"lit_{name}"][on_plane]
        assert (band & shadow).sum() >= 5 and (band & ~shadow).sum() > 100
        assert (lit[band] == ~shadow[band]).sum() >= band.sum() - 1
        assert (aux[f"E_{name}"][on_plane][band & shadow] == 0).all()


def test_apriltags_render_at_true_marker_corners():
    """scene.markers (world corners, ArUco order) must match what a detector finds in the image."""
    cal = nominal_calibration()
    sc = sphere_on_plane()
    prm = cv2.aruco.DetectorParameters()
    prm.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11), prm)
    with Renderer(cal, None, sc, RenderParams(dots_on=False, lines_on=False, ambient=120.0, seed=8)) as r:
        left, _, _ = r.render(POSE)
    corners, ids, _ = det.detectMarkers(left)
    assert ids is not None and len(ids) >= 4 and set(ids.ravel()) <= set(sc.markers)
    T_cw = np.linalg.inv(POSE)
    for c, i in zip(corners, ids.ravel()):
        Pc = sc.markers[int(i)] @ T_cw[:3, :3].T + T_cw[:3, 3]
        uv = Pc[:, :2] / Pc[:, 2:] * np.diag(cal.stereo.K_rect)[:2] + cal.stereo.K_rect[:2, 2]
        c = c.reshape(4, 2)
        assert np.linalg.norm(c.mean(0) - uv.mean(0)) < 0.3  # centre (refinement pulls L-corners inward ~1 %)
        assert np.abs(c - uv).max() < 0.8


def test_brightness_levels_at_250mm():
    """Dots ~80-200 DN above ambient, lines saturating near 255 at 250 mm; dimmer at 400 mm."""
    cal = nominal_calibration()
    wall = Scene([Rect(make_se3(np.diag([1.0, -1.0, -1.0])), (-800, 800, -800, 800), ConstantAlbedo(0.7))])
    quiet = dict(noise_sigma=0.0, shot_noise=0.0, speckle=0.0, seed=6)

    def shot(dist, **kw):
        T = scanner_pose([30.0, 0.0, dist], [30.0, 0.0, 0.0], up=(0.0, 1.0, 0.0))
        with Renderer(cal, None, wall, RenderParams(**{**quiet, **kw})) as r:
            return r.render(T)[0].astype(np.float64)

    amb = shot(250.0, dots_on=False, lines_on=False)
    dots = shot(250.0, lines_on=False)
    diff = dots - amb
    peaks = diff[(diff == cv2.dilate(diff, np.ones((5, 5), np.uint8))) & (diff > 20)]
    assert len(peaks) > 5000  # ~10k dots, most inside the image
    assert 80 <= np.median(peaks) <= 200
    assert np.percentile(peaks, 10) >= 60 and np.percentile(peaks, 90) <= 220
    lines = shot(250.0, dots_on=False)
    assert lines.max() >= 250.0
    far = shot(400.0, dots_on=False)
    assert 60.0 < far.max() < 250.0


def test_rectified_render_matches_rectified_raw_render():
    cal = nominal_calibration((640, 400), realistic=True)
    st = cal.stereo
    scene = charuco_board()
    T = scanner_pose([20.0, -60.0, 330.0], [0.0, 0.0, 0.0])
    params = RenderParams(ambient=170.0, dots_on=False, lines_on=False, noise_sigma=0.0, shot_noise=0.0, vignetting=0.0, seed=7)
    with Renderer(cal, None, scene, params) as r:
        raw_l, raw_r, depth_raw = r.render(T)
        rect_l, rect_r, depth_rect = r.render(T, rectified=True)
    assert np.array_equal(depth_raw, depth_rect)
    for raw, rect, (K, D, Rr, P) in ((raw_l, rect_l, (st.K1, st.D1, st.R1, st.P1)), (raw_r, rect_r, (st.K2, st.D2, st.R2, st.P2))):
        mx, my = cv2.initUndistortRectifyMap(K, D, Rr, P, st.image_size, cv2.CV_32FC1)
        via_raw = cv2.remap(raw, mx, my, cv2.INTER_LINEAR).astype(np.float64)[20:-20, 20:-20]
        direct = rect.astype(np.float64)[20:-20, 20:-20]
        ncc = np.corrcoef(via_raw.ravel(), direct.ravel())[0, 1]
        assert ncc > 0.97
        assert not np.array_equal(raw, rect)  # distortion really changes the raw image


def test_render_is_deterministic_and_seeded():
    cal = nominal_calibration((320, 200))
    sc = sphere_on_plane()
    a = Renderer(cal, None, sc, RenderParams(seed=11)).render(POSE)
    b = Renderer(cal, None, sc, RenderParams(seed=11)).render(POSE)
    c = Renderer(cal, None, sc, RenderParams(seed=12)).render(POSE)
    assert all(np.array_equal(x, y) for x, y in zip(a, b))
    assert not np.array_equal(a[0], c[0]) and np.array_equal(a[2], c[2])


def test_render_speed_640x400():
    cal = nominal_calibration((640, 400))
    with Renderer(cal, None, sphere_on_plane(), RenderParams()) as r:
        r.render(POSE)
        t0 = time.perf_counter()
        for _ in range(3):
            r.render(POSE)
        dt = (time.perf_counter() - t0) / 3
    assert dt < 1.5


@pytest.mark.slow
def test_render_speed_full_resolution():
    cal = nominal_calibration()
    with Renderer(cal, None, sphere_on_plane(), RenderParams()) as r:
        r.render(POSE)
        t0 = time.perf_counter()
        for _ in range(3):
            r.render(POSE)
        dt = (time.perf_counter() - t0) / 3
    assert dt < 3.0  # target 1.5 s; lenient for loaded CI machines
