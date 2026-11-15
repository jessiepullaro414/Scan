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
from ohs.vision.laser import line_family

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


# --------------------------------------------------------------------------- several lasers per family
def sheet(name: str, origin, aim_deg: float) -> LaserPlane:
    """A laser sheet from its emitter position and aim. Vertical family: fan along camera y, turned
    ``aim_deg`` toward -x; horizontal family: fan along camera x, tilted ``aim_deg`` toward +y."""
    a = np.radians(aim_deg)
    n = np.array([np.cos(a), 0.0, np.sin(a)]) if name.startswith("vertical") else np.array([0.0, -np.cos(a), np.sin(a)])
    d = -float(n @ np.asarray(origin, dtype=np.float64))
    return LaserPlane(name, n, d) if d <= 0 else LaserPlane(name, -n, -d)


# the 4-laser housing: a mirrored vertical laser on the left, a low horizontal one, all aimed at the centre line
FOUR = {**TRUE,
        "vertical_2": sheet("vertical_2", (-56.0, 0.0, -1.4), -np.degrees(np.arctan(86.0 / 272.0))),
        "horizontal_2": sheet("horizontal_2", (88.0, 56.0, -1.2), -np.degrees(np.arctan(56.0 / 272.0)))}
# another build: both horizontal lasers above the cameras, the second one tilted up, so that their
# lines swap places on the board beyond 175 mm; a straight-ahead right laser
OTHER = {"vertical": sheet("vertical", (90.0, 0.0, 0.0), 0.0),
         "vertical_2": sheet("vertical_2", (-40.0, 0.0, 0.0), -8.0),
         "horizontal": sheet("horizontal", (30.0, -60.0, 0.0), 10.0),
         "horizontal_2": sheet("horizontal_2", (30.0, -20.0, 0.0), -3.0)}
NAMES4 = ("vertical", "horizontal", "vertical_2", "horizontal_2")


def make_family_obs(renderer, T_cb, rng, planes):
    """An observation whose lines are stored per family, as the extraction gives them
    (``LaserLines.as_dict()``: every line of a family in one array)."""
    img = renderer.render(T_cb, rng=rng)
    lines: dict[str, list] = {}
    for name, p in planes.items():
        lines.setdefault(line_family(name), []).append(line_pixels(T_cb, p, rng))
    return make_observation(img, K, SPEC, {f: np.vstack(v) for f, v in lines.items()})


def plane_error(p: LaserPlane, true: LaserPlane) -> tuple[float, float]:
    return float(np.degrees(np.arccos(np.clip(abs(p.normal @ true.normal), -1, 1)))), float(p.d - true.d)


@pytest.mark.parametrize("layout", ["four", "other"])
def test_fit_laser_planes_separates_and_names_two_lines_per_family(renderer, layout):
    truth = FOUR if layout == "four" else OTHER
    rng = np.random.default_rng(11)
    obs = [make_family_obs(renderer, random_pose(rng), rng, truth) for _ in range(8)]
    assert all(o is not None for o in obs)
    planes, rep = fit_laser_planes(obs, K, names=NAMES4)
    assert rep["ok"] and set(planes) == set(NAMES4), {n: e.get("reason") for n, e in rep["lasers"].items()}
    for name, true in truth.items():
        p = planes[name]
        ang, dd = plane_error(p, true)
        assert ang < 0.1 and abs(dd) < 0.3, (layout, name, ang, dd)  # the tolerance of the two-laser fit above
        assert p.name == name and p.d <= 0
        e = rep["lasers"][name]
        assert e["ok"] and e["n_observations"] >= 3 and e["n_points"] > 0 and e["rms_mm"] < 0.3
        assert e["intercept_mm"] == pytest.approx(-p.d / (p.normal[0] if name.startswith("vertical") else p.normal[1]))
    for fam in ("vertical", "horizontal"):
        f = rep["families"][fam]
        assert f["names"] == [n for n in NAMES4 if n.startswith(fam)] and f["planes_found"] == 2
    # the order of the families does not matter; within a family the first name is the sheet that
    # starts at the largest x (vertical) or smallest y (horizontal)
    planes2, _ = fit_laser_planes(obs, K, names=("horizontal", "vertical", "horizontal_2", "vertical_2"))
    assert list(planes2) == ["horizontal", "vertical", "horizontal_2", "vertical_2"]
    for name in NAMES4:
        assert np.allclose(planes2[name].normal, planes[name].normal) and planes2[name].d == pytest.approx(planes[name].d)
    swapped, _ = fit_laser_planes(obs, K, names=("vertical_2", "horizontal", "vertical", "horizontal_2"))
    for a, b in (("vertical", "vertical_2"), ("vertical_2", "vertical")):
        assert np.allclose(swapped[a].normal, planes[b].normal) and swapped[a].d == pytest.approx(planes[b].d)
    # the check measures each plane on its own line
    new = [make_family_obs(renderer, random_pose(rng), rng, truth) for _ in range(3)]
    chk = check_laser_planes(planes, new, K)
    assert chk["ok"] and set(chk["lasers"]) == set(NAMES4)
    for name in NAMES4:
        e = chk["lasers"][name]
        assert e["median_abs_mm"] < 0.2 and abs(e["median_mm"]) < 0.1 and e["n_observations"] == 3, (name, e)
    moved = dict(planes)
    p = planes["vertical_2"]
    moved["vertical_2"] = LaserPlane("vertical_2", G.so3_exp([0, np.radians(1.0), 0]) @ p.normal, p.d)
    bad = check_laser_planes(moved, new, K)
    assert not bad["ok"] and bad["lasers"]["vertical_2"]["median_abs_mm"] > 1.0 and bad["lasers"]["vertical"]["ok"]


def test_fit_reports_a_family_with_fewer_planes_than_names(renderer):
    """Two lasers drawn, four named: the second line of a family is not there, so which laser the
    one plane belongs to cannot be told; none of the family is fitted and the report says why."""
    rng = np.random.default_rng(11)
    obs = [make_family_obs(renderer, random_pose(rng), rng, TRUE) for _ in range(6)]
    planes, rep = fit_laser_planes(obs, K, names=NAMES4)
    assert planes == {} and not rep["ok"]
    for name in NAMES4:
        assert not rep["lasers"][name]["ok"] and "1 of 2" in rep["lasers"][name]["reason"], name
    # with one name per family it is the two-laser fit
    planes, rep = fit_laser_planes(obs, K, names=("vertical", "horizontal"))
    assert rep["ok"] and set(planes) == {"vertical", "horizontal"}
