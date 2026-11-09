"""Laser-plane calibration on synthetic rectified board views (area C)."""
import cv2
import numpy as np
import pytest

from ohs import geometry as G
from ohs.calib.board import BoardViewRenderer, CharucoSpec, board_outline_uv
from ohs.calib.laser_planes import (
    LaserBoardObservation,
    board_points_from_line,
    check_laser_planes,
    fit_laser_planes,
    make_observation,
)
from ohs.types import LaserPlane

SIZE = (1280, 800)
SPEC = CharucoSpec()
K = np.array([[720.0, 0, 639.5], [0, 720.0, 399.5], [0, 0, 1]])
# nominal CAD planes (ARCHITECTURE.md section 7)
TRUE = {
    "vertical": LaserPlane("vertical", np.array([0.9473, 0.0, 0.3205]), -109.8),
    "horizontal": LaserPlane("horizontal", np.array([0.0, -0.9594, 0.2822]), -71.67),
}


def random_pose(rng):
    Z = rng.uniform(220, 400)
    u, v = rng.uniform(600, 700), rng.uniform(360, 440)
    P = Z * np.linalg.inv(K) @ np.array([u, v, 1.0])
    rx, ry, rz = np.radians(rng.uniform(-35, 35)), np.radians(rng.uniform(-35, 35)), np.radians(rng.uniform(-20, 20))
    R = G.so3_exp([0, 0, rz]) @ G.so3_exp([0, ry, 0]) @ G.so3_exp([rx, 0, 0])
    c = np.array([SPEC.size_mm[0] / 2, SPEC.size_mm[1] / 2, 0.0])
    return G.make_se3(R, P - R @ c)


def plane_board_line(T_cb, plane, step_mm=0.4):
    """3D points of the laser-plane / board-plane intersection inside the board rectangle."""
    R, t = T_cb[:3, :3], T_cb[:3, 3]
    nb = R[:, 2]
    u = np.cross(plane.normal, nb)
    u /= np.linalg.norm(u)
    p0 = np.linalg.solve(np.vstack([plane.normal, nb, u]), [-plane.d, nb @ t, 0.0])
    X = p0 + np.arange(-700, 700, step_mm)[:, None] * u
    Xb = (X - t) @ R
    W, H = SPEC.size_mm
    return X[(Xb[:, 0] > 0) & (Xb[:, 0] < W) & (Xb[:, 1] > 0) & (Xb[:, 1] < H)]


def line_pixels(T_cb, plane, rng, noise_px=0.2, outlier_frac=0.03):
    """Projected intersection line + noise, a few outliers on the board and a wall segment off it."""
    uv = G.project_points(plane_board_line(T_cb, plane), K)
    uv = uv + rng.normal(0, noise_px, uv.shape)
    lo, hi = uv.min(0), uv.max(0)
    n_out = max(3, int(outlier_frac * len(uv)))
    outliers = rng.uniform(lo - 40, hi + 40, size=(n_out, 2))
    # the sheet continues on a wall 700 mm away, visible only around the board (paper incl.);
    # those pixels are off the board and must be ignored
    s = np.linspace(-300, 300, 400)
    n = plane.normal
    a = np.cross(n, [0, 0, 1.0])
    p0 = np.linalg.lstsq(np.vstack([n, [0, 0, 1.0]]), [-plane.d, 700.0], rcond=None)[0]
    wall = G.project_points(p0 + s[:, None] * a / np.linalg.norm(a), K)
    paper = board_outline_uv(T_cb, SPEC, K, margin_mm=-8.0).astype(np.float32).reshape(-1, 1, 2)
    hidden = np.array([cv2.pointPolygonTest(paper, (float(x), float(y)), False) >= 0 for x, y in wall])
    return np.vstack([uv, outliers, wall[~hidden]])


def make_obs(renderer, T_cb, rng, planes=TRUE):
    img = renderer.render(T_cb, rng=rng)
    lines = {name: line_pixels(T_cb, p, rng) for name, p in planes.items()}
    return make_observation(img, K, SPEC, lines)


@pytest.fixture(scope="module")
def renderer():
    return BoardViewRenderer(SPEC, K, SIZE)


@pytest.fixture(scope="module")
def observations(renderer):
    rng = np.random.default_rng(11)
    obs = [make_obs(renderer, random_pose(rng), rng) for _ in range(8)]
    assert all(o is not None for o in obs)
    return obs


def test_board_points_lie_on_both_planes(observations):
    o = observations[0]
    for name, plane in TRUE.items():
        P = board_points_from_line(o, K, name)
        assert len(P) > 100
        nb = o.T_cb[:3, 2]
        assert np.allclose((P - o.T_cb[:3, 3]) @ nb, 0, atol=1e-9)  # on the board plane
        assert np.median(np.abs(plane.distance(P))) < 0.2  # and (up to noise/outliers) on the laser sheet
    empty = LaserBoardObservation(o.T_cb, o.board_polygon_uv, {})
    assert board_points_from_line(empty, K, "vertical").shape == (0, 3)


def test_fit_laser_planes_recovers_planes(observations):
    planes, rep = fit_laser_planes(observations, K)
    assert rep["ok"] and set(planes) == {"vertical", "horizontal"}
    for name, true in TRUE.items():
        p = planes[name]
        ang = np.degrees(np.arccos(np.clip(abs(p.normal @ true.normal), -1, 1)))
        assert ang < 0.1, (name, ang)
        assert abs(p.d - true.d) < 0.3, (name, p.d, true.d)
        assert p.d <= 0 and p.normal @ true.normal > 0
        e = rep["lasers"][name]
        assert e["ok"] and not e["degenerate"] and e["n_observations"] == 8 and e["rms_mm"] < 0.3
        assert e["inlier_fraction"] > 0.85


def test_degenerate_single_pose_is_reported(renderer):
    rng = np.random.default_rng(5)
    T_cb = random_pose(rng)
    obs = [make_obs(renderer, T_cb, rng) for _ in range(4)]  # board never moved
    planes, rep = fit_laser_planes(obs, K)
    assert planes == {} and not rep["ok"]
    for name in TRUE:
        e = rep["lasers"][name]
        assert not e["ok"] and e["degenerate"]
        assert e["n_distinct_poses"] == 1 and "collinear" in e["reason"]
    # too few observations is reported too
    planes, rep = fit_laser_planes(obs[:2], K, names=("vertical",))
    assert planes == {} and "need >= 3" in rep["lasers"]["vertical"]["reason"]


def test_check_laser_planes(observations, renderer):
    planes, _ = fit_laser_planes(observations, K)
    rng = np.random.default_rng(23)
    new = [make_obs(renderer, random_pose(rng), rng) for _ in range(3)]
    rep = check_laser_planes(planes, new, K)
    assert rep["ok"]
    for name in TRUE:
        e = rep["lasers"][name]
        assert e["median_abs_mm"] < 0.2 and abs(e["median_mm"]) < 0.1 and e["n_observations"] == 3
        assert e["inlier_fraction"] > 0.9
    # a laser that moved by 1 deg is flagged
    p = planes["vertical"]
    moved = {"vertical": LaserPlane("vertical", G.so3_exp([0, np.radians(1.0), 0]) @ p.normal, p.d)}
    bad = check_laser_planes(moved, new, K)
    assert not bad["ok"] and bad["lasers"]["vertical"]["median_abs_mm"] > 1.0
    assert "recalibrate" in bad["lasers"]["vertical"]["verdict"]
