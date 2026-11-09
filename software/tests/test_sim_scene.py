"""Simulator scene: primitive intersections, textures, presets."""
import math

import cv2
import numpy as np
import pytest

from ohs.geometry import make_se3, so3_exp
from ohs.sim.scene import (
    GRAVITY_W,
    Box,
    Cylinder,
    HeightField,
    NoiseAlbedo,
    Rect,
    Sphere,
    apriltag_texture,
    bracket,
    car_panel,
    charuco_board,
    charuco_texture,
    make_scene,
    sphere_on_plane,
)


def _unit(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def test_sphere_and_rect_hits_are_analytic():
    rng = np.random.default_rng(0)
    s = Sphere((10.0, -5.0, 40.0), 40.0)
    o = np.array([0.0, 0.0, 400.0])
    d = _unit(np.column_stack([rng.uniform(-0.3, 0.3, 2000), rng.uniform(-0.3, 0.3, 2000), -np.ones(2000)]))
    t = s.intersect(o, d)
    hit = np.isfinite(t)
    assert 100 < hit.sum() < 2000
    p = o + t[hit, None] * d[hit]
    assert np.abs(np.linalg.norm(p - s.center, axis=1) - 40.0).max() < 1e-9
    # rays that miss really pass farther than the radius
    oc = o - s.center
    miss_dist = np.linalg.norm(np.cross(d[~hit], -oc), axis=1)
    assert miss_dist.min() >= 40.0 - 1e-9
    # rectangle: rotated, bounded, two-sided, normal is local -z
    R = so3_exp(np.array([0.3, -0.2, 0.5]))
    rect = Rect(make_se3(R, [5.0, 3.0, -2.0]), (-50, 30, -20, 60))
    # rays from both sides aimed at points of the plane around the rectangle
    q = np.column_stack([rng.uniform(-80, 60, 3000), rng.uniform(-50, 90, 3000), np.zeros(3000)])
    target = q @ R.T + rect.t
    o2 = target + _unit(rng.normal(size=(3000, 3))) * 250.0
    d2 = _unit(target - o2)
    t2 = rect.intersect(o2, d2)
    h2 = np.isfinite(t2)
    inside = (q[:, 0] >= -50) & (q[:, 0] <= 30) & (q[:, 1] >= -20) & (q[:, 1] <= 60)
    assert np.array_equal(h2, inside) and h2.sum() > 500
    assert np.allclose(t2[h2], 250.0)
    n, _ = rect.shade(o2[h2] + t2[h2, None] * d2[h2], np.zeros(h2.sum()))
    assert np.allclose(n, -R[:, 2])


def test_box_and_cylinder_faces_and_normals():
    R = so3_exp(np.array([0.0, 0.0, math.radians(30)]))
    box = Box(make_se3(R, [10.0, 20.0, 5.0]), (15.0, 10.0, 5.0))
    o = np.array([10.0, 20.0, 100.0])
    t = box.intersect(o, np.array([[0.0, 0.0, -1.0]]))
    assert t[0] == pytest.approx(90.0)  # top face at z = 10
    n, _ = box.shade(o + t[0] * np.array([[0, 0, -1.0]]), np.zeros(1))
    assert np.allclose(n, [[0, 0, 1]])
    side_dir = R @ np.array([-1.0, 0.0, 0.0])
    o2 = np.array([10.0, 20.0, 5.0]) - 100 * side_dir
    t2 = box.intersect(o2, side_dir[None])
    assert t2[0] == pytest.approx(85.0)  # local +x face at 15 mm
    cyl = Cylinder(make_se3(so3_exp(np.array([math.pi / 2, 0, 0])), [0.0, 0.0, 50.0]), 8.0, 20.0)  # axis along world y
    t3 = cyl.intersect(np.array([0.0, 0.0, 200.0]), np.array([[0.0, 0.0, -1.0]]))
    assert t3[0] == pytest.approx(200.0 - 58.0)
    t4 = cyl.intersect(np.array([0.0, 100.0, 50.0]), np.array([[0.0, -1.0, 0.0]]))  # into a cap
    assert t4[0] == pytest.approx(80.0)
    n4, _ = cyl.shade(np.array([[0.0, 20.0, 50.0]]), np.zeros(1))
    assert np.allclose(np.abs(n4), [[0, 1, 0]], atol=1e-12)
    assert not np.isfinite(cyl.intersect(np.array([30.0, 0.0, 200.0]), np.array([[0.0, 0.0, -1.0]]))[0])


def test_heightfield_newton_hits_surface_and_holes():
    def h(x, y):
        return -(x * x) / 1800 - 0.1 * np.sqrt((y - 10) ** 2 + 9), -x / 900, -0.1 * (y - 10) / np.sqrt((y - 10) ** 2 + 9)

    T = make_se3(so3_exp(np.array([0.1, -0.05, 0.3])), [0.0, 0.0, 50.0])
    hf = HeightField(T, h, (-100, 100, -80, 80), (-15.0, 0.0), holes=[(40.0, -30.0, 10.0)])
    rng = np.random.default_rng(1)
    o = np.array([20.0, -10.0, 350.0])
    d = _unit(np.column_stack([rng.uniform(-0.45, 0.45, 5000), rng.uniform(-0.4, 0.4, 5000), -np.ones(5000)]))
    t = hf.intersect(o, d)
    hit = np.isfinite(t)
    assert hit.sum() > 1000
    pl = (o + t[hit, None] * d[hit] - hf.t) @ hf.R
    z, _, _ = h(pl[:, 0], pl[:, 1])
    assert np.abs(pl[:, 2] - z).max() < 1e-8
    assert ((pl[:, 0] - 40) ** 2 + (pl[:, 1] + 30) ** 2 > 100 - 1e-6).all()
    # the hole really is a hole: aim at its centre
    c_hole = T[:3, :3] @ np.array([40.0, -30.0, h(np.array([40.0]), np.array([-30.0]))[0][0]]) + T[:3, 3]
    assert not np.isfinite(hf.intersect(o, _unit(c_hole - o)[None])[0])


def test_scene_cast_and_occlusion():
    sc = sphere_on_plane(tags=False)
    o = np.array([0.0, 0.0, 300.0])
    d = np.array([[0.0, 0.0, -1.0], [0.5, 0.0, -1.0], [0.0, 0.0, 1.0]])
    d = _unit(d)
    hit = sc.cast(o, d, 1e-3)
    assert hit.obj.tolist() == [1, 0, -1]
    assert hit.t[0] == pytest.approx(220.0) and hit.t[1] == pytest.approx(300 * math.sqrt(1.25))
    assert (np.einsum("ij,ij->i", hit.normal[:2], d[:2]) < 0).all()
    assert 0.4 < hit.albedo[1] < 0.7 and 0.7 < hit.albedo[0] < 0.9
    # shadow ray from under the sphere toward the sky is blocked; one beside it is free
    occ = sc.occluded(np.array([[0.0, 0.0, 0.01], [100.0, 0.0, 0.01]]), np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 1.0]]), np.array([500.0, 500.0]))
    assert occ.tolist() == [True, False]
    assert np.allclose(GRAVITY_W, [0, 0, -9.81])


def test_noise_albedo_is_low_contrast_and_surface_fixed():
    alb = NoiseAlbedo(mean=0.6, contrast=0.03, scale_mm=4.0, seed=3)
    rng = np.random.default_rng(0)
    p = rng.uniform(-300, 300, (20000, 3))
    n = np.tile([0.0, 0.0, 1.0], (20000, 1))
    a = alb(p, n, np.zeros(20000))
    assert a.mean() == pytest.approx(0.6, abs=0.005) and 0.01 < a.std() < 0.03
    assert np.array_equal(a, alb(p, n, np.zeros(20000)))
    # correlated over ~1 mm, decorrelated over ~20 mm
    a1 = alb(p + [0.3, 0.0, 0.0], n, np.zeros(20000))
    a20 = alb(p + [20.0, 0.0, 0.0], n, np.zeros(20000))
    assert np.corrcoef(a, a1)[0, 1] > 0.9 and abs(np.corrcoef(a, a20)[0, 1]) < 0.2


def test_charuco_texture_matches_opencv_board_frame():
    tex = charuco_texture()
    assert tex.shape == (1600, 2200) and tex.dtype == np.uint8
    board = cv2.aruco.CharucoBoard((11, 8), 20.0, 15.0, cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100))
    small = cv2.resize(tex, (1100, 800), interpolation=cv2.INTER_AREA)
    det = cv2.aruco.CharucoDetector(board)
    corners, ids, _, _ = det.detectBoard(small)
    assert ids is not None and len(ids) == 70
    obj = board.getChessboardCorners()[ids.ravel()]
    # board point (x, y) mm -> texture pixel x * px_per_mm - 0.5 (scaled by 0.5 here)
    expected = obj[:, :2] * 5.0 - 0.5
    assert np.abs(corners.reshape(-1, 2) - expected).max() < 0.1


def test_apriltag_texture_and_sphere_scene_markers():
    tex = apriltag_texture(4, tag_mm=20, px_per_mm=10)
    assert tex.shape == (200, 200)
    padded = cv2.copyMakeBorder(tex, 50, 50, 50, 50, cv2.BORDER_CONSTANT, value=255)
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11), cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(padded)
    assert ids is not None and ids.ravel().tolist() == [4]
    assert np.allclose(corners[0].reshape(4, 2), [[49.5, 49.5], [249.5, 49.5], [249.5, 249.5], [49.5, 249.5]], atol=1.0)
    sc = sphere_on_plane()
    assert sorted(sc.markers) == [0, 1, 2, 3, 4, 5]
    for c in sc.markers.values():
        assert np.allclose(c[:, 2], 0.0)
        assert np.allclose(np.linalg.norm(np.diff(np.vstack([c, c[:1]]), axis=0), axis=1), 20.0)
        # seen from above (+Z), TL -> TR -> BR runs clockwise: normal of (TR-TL) x (BL-TL) points down (into the table)
        assert np.cross(c[1] - c[0], c[3] - c[0])[2] < 0
        assert 70 < np.linalg.norm(c.mean(0)[:2]) < 120


def test_presets_build_and_are_hit_from_above():
    for name in ("sphere_on_plane", "car_panel", "bracket"):
        sc = make_scene(name, seed=1)
        assert sc.name == name and sc.primitives
        rng = np.random.default_rng(0)
        o = sc.target + np.array([0.0, 0.0, 250.0])
        d = _unit(np.column_stack([rng.uniform(-0.3, 0.3, 500), rng.uniform(-0.3, 0.3, 500), -np.ones(500)]))
        hit = sc.cast(o, d, 1e-3)
        assert (hit.obj >= 0).all() and np.isfinite(hit.t).all()
        assert ((hit.albedo > 0.02) & (hit.albedo < 0.95)).all()
    panel = car_panel(tags=True)
    assert sorted(panel.markers) == list(range(6))
    assert {p.name for p in bracket().primitives} >= {"table", "base", "upright", "shaft"}
    with pytest.raises(ValueError):
        make_scene("nope")


def test_charuco_board_scene_frame():
    T_wb = make_se3(so3_exp(np.array([0.2, 0.1, 0.4])), [10.0, -20.0, 5.0])
    sc = charuco_board(T_wb, squares_x=5, squares_y=4, square_mm=30.0, marker_mm=22.0)
    assert sc.meta["board"]["squares_x"] == 5 and np.allclose(sc.meta["board"]["T_wb"], T_wb)
    # rays from the front (-z side of the board) through square centres see the printed colour
    tex = charuco_texture(5, 4, 30.0, 22.0)
    for (i, j) in ((0, 0), (1, 0), (2, 3)):
        x, y = 30.0 * i + 15.0, 30.0 * j + 15.0
        p = T_wb @ np.array([x, y, 0.0, 1.0])
        o = p[:3] - 200 * T_wb[:3, 2]
        hit = sc.cast(o, T_wb[:3, 2][None], 1e-3)
        expected = 0.04 + 0.81 * tex[int(y * 10), int(x * 10)] / 255.0
        assert hit.obj[0] == 0 and hit.t[0] == pytest.approx(200.0) and hit.albedo[0] == pytest.approx(expected, abs=0.01)
    # white paper margin outside the squares
    p = T_wb @ np.array([-5.0, 10.0, 0.0, 1.0])
    assert sc.cast(p[:3] - 100 * T_wb[:3, 2], T_wb[:3, 2][None], 1e-3).albedo[0] == pytest.approx(0.85, abs=0.01)
    with pytest.raises(TypeError):
        charuco_board(None, squares=3)
    sc0 = charuco_board()
    assert np.allclose(sc0.target, 0.0)
