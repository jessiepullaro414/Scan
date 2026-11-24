"""``tools/hole_rim_study.py`` (ENGINE_PLAN E-D3, X15's first test): the hole read from the camera images, checked
on synthetic images of a known circle (a dotted face, a dark hole, a blurred edge drawn analytically; no
rendering, no scan)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from tools import hole_rim_study as H  # noqa: E402

K = np.array([[720.0, 0.0, 640.0], [0.0, 720.0, 400.0], [0.0, 0.0, 1.0]])
Z0 = 250.0  # the face: the plane z = Z0 in frame C, facing the cameras
SIZE = (800, 1280)


def _cams():
    left = H.Camera(K, np.zeros(5), np.eye(3), np.zeros(3))
    right = H.Camera(K, np.zeros(5), np.eye(3), np.array([-60.0, 0.0, 0.0]))  # x_right = x_left + T
    return left, right


def _shade(X, Y, cx, cy, r, dots, hole_grey=10.0):
    """The face: 30 grey with projector dots on a jittered 3.5 mm grid (``dots`` seeds the jitter: the dots are
    fixed to the scanner, so each frame has them elsewhere on the face); the hole: ``hole_grey`` (10: a dark
    background)."""
    g = 3.5
    i, j = np.floor(X / g), np.floor(Y / g)
    rng = np.random.default_rng(dots)
    jit = rng.uniform(0.25, 0.75, size=(64, 64, 2))
    dx = (i + jit[i.astype(int) % 64, j.astype(int) % 64, 0]) * g - X
    dy = (j + jit[i.astype(int) % 64, j.astype(int) % 64, 1]) * g - Y
    face = 30.0 + 120.0 * np.exp(-(dx * dx + dy * dy) / (2 * 0.3 ** 2))
    return np.where((X - cx) ** 2 + (Y - cy) ** 2 < r * r, hole_grey, face)


def _image(cam: H.Camera, cx: float, cy: float, r: float, dots: int, ss: int = 4, hole_grey: float = 10.0) -> np.ndarray:
    """The camera's image of the face and the hole (supersampled ``ss`` x ``ss`` a pixel, then a 0.6 px blur), drawn
    in a window round the hole; constant face grey elsewhere."""
    import cv2

    img = np.full(SIZE, 30.0, np.float32)
    C = cam.centre
    uc, vc = (K @ (cam.R @ np.array([cx, cy, Z0]) + cam.t))[:2] / Z0
    half = int(40.0 * K[0, 0] / Z0)
    u0, v0 = int(uc) - half, int(vc) - half
    n = 2 * half
    o = (np.arange(n * ss) + 0.5) / ss - 0.5
    uu, vv = np.meshgrid(u0 + o, v0 + o)
    d = np.stack([(uu - K[0, 2]) / K[0, 0], (vv - K[1, 2]) / K[1, 1], np.ones_like(uu)], -1) @ cam.R  # rays, frame C
    s = (Z0 - C[2]) / d[..., 2]
    X, Y = C[0] + s * d[..., 0], C[1] + s * d[..., 1]
    win = _shade(X, Y, cx, cy, r, dots, hole_grey).reshape(n, ss, n, ss).mean(axis=(1, 3)).astype(np.float32)
    img[v0:v0 + n, u0:u0 + n] = win
    return cv2.GaussianBlur(img, (0, 0), 0.6)


def _read(cx: float, cy: float, r: float, dots: int = 0):
    cams = _cams()
    imgs = [_image(c, cx, cy, r, dots) for c in cams]
    depth = np.full(SIZE, Z0, np.float32)  # flat everywhere: as if the matcher had filled the hole
    sigma = np.full(SIZE, 0.5, np.float32)
    click = np.array([cx + r + 1.5, cy, Z0])
    return H.read_frame(imgs, cams, depth, sigma, K, click)


def _frames(cx: float, cy: float, r: float, n: int = 5) -> dict:
    """The reading over ``n`` frames, each with the dots elsewhere, at one pose (frame C = the world)."""
    fs = [_read(cx, cy, r, dots) for dots in range(n)]
    assert all(f.ok for f in fs), [f.why for f in fs]
    return H.combine(fs, [np.eye(4)] * n)


def test_a_known_circle_is_read_within_a_twentieth_of_a_millimetre():
    cx, cy, r = 83.13, -42.07, 5.0  # off axis (the cameras outside its footprint), off the pixel grid
    a = _frames(cx, cy, r)
    assert "refused" not in a
    assert abs(a["d"] - 2 * r) < 0.05, a["d"]
    assert np.linalg.norm(np.asarray(a["centre"]) - [cx, cy, Z0]) < 0.05, a["centre"]
    assert abs(a["d_all"] - 2 * r) < 0.05  # a face without thickness: the wall-seen sectors agree
    assert a["pm"] < 0.05


def test_a_sub_pixel_move_of_the_hole_is_followed():
    a = _frames(83.13, -42.07, 5.0)
    b = _frames(83.13 + 0.1, -42.07, 5.0)  # 0.1 mm is 0.29 px here
    assert abs((b["centre"][0] - a["centre"][0]) - 0.1) < 0.03
    assert abs(b["centre"][1] - a["centre"][1]) < 0.03
    assert abs(b["d"] - a["d"]) < 0.03


def test_a_large_hole_too():
    a = _frames(-75.4, 31.7, 12.5)
    assert abs(a["d"] - 25.0) < 0.05, a["d"]


def test_the_step_is_found_at_its_half_level():
    rng = np.random.default_rng(1)
    r = rng.uniform(4.0, 8.0, 4000)
    edge = 6.123
    from math import erf

    I = 10.0 + 20.0 * np.array([0.5 * (1 + erf((x - edge) / (0.2 * math.sqrt(2)))) for x in r])
    I += rng.normal(0, 1.0, len(r))
    cp, half = H.sector_edge(r, I)
    assert abs(half - edge) < 0.02
    assert abs(cp - edge) < 0.2


def test_the_right_camera_sits_where_the_calibration_puts_it():
    R1 = np.array([[math.cos(0.01), -math.sin(0.01), 0], [math.sin(0.01), math.cos(0.01), 0], [0, 0, 1]])
    cal = {"K1": K.tolist(), "D1": [0] * 5, "K2": K.tolist(), "D2": [0] * 5, "R": np.eye(3).tolist(),
           "T": [-60.0, 0.0, 0.0], "R1": R1.tolist()}
    left, right = H.stereo_cameras(cal)
    assert np.allclose(left.centre, 0.0)
    assert np.allclose(right.centre, R1 @ np.array([60.0, 0.0, 0.0]))


def test_the_diameter_does_not_follow_the_pose_and_too_few_frames_refuse():
    f = _read(83.13, -42.07, 5.0)
    shift = np.eye(4)
    shift[:3, 3] = [5.0, -3.0, 1.0]
    a = H.combine([f] * 6, [np.eye(4)] * 6)
    b = H.combine([f] * 6, [shift] * 6)
    assert a["d"] == b["d"]
    assert np.allclose(np.asarray(b["centre"]) - a["centre"], [5.0, -3.0, 1.0])
    assert "refused" in H.combine([f] * 3, [np.eye(4)] * 3)


# --------------------------------------------------------------------------- step 2: one model of the hole
def _model_steps(R=6.0, t=3.0, c=(0.3, -0.2), clutter=0, seed=0):
    """Candidate steps a 3 mm plate would give two cameras 250 mm above it, off to one side: the top rim all round,
    the exit rim where it is seen inside the top rim, plus ``clutter`` stray steps."""
    rng = np.random.default_rng(seed)
    Cip = np.array([[120.0, 40.0], [175.0, 40.0]])
    h = np.array([230.0, 232.0])
    c = np.asarray(c)
    p = np.array([c[0], c[1], R, t])
    ce, Re = H.exit_circles(p, Cip, h)
    rows = []
    for ci in (0, 1):
        for a in np.radians(np.arange(3.0, 360.0, 6.0)):
            w = np.array([math.cos(a), math.sin(a)])
            rows.append((*(c + (R + rng.normal(0, 0.02)) * w), ci, 10.0))
            q = ce[ci] + Re[ci] * w
            if np.hypot(*(q - c)) < R - 0.3:
                rows.append((*(q + rng.normal(0, 0.02, 2)), ci, 8.0))
    for _ in range(clutter):
        a, r = rng.uniform(0, 2 * math.pi), rng.uniform(R - 3, R + 2)
        rows.append((c[0] + r * math.cos(a), c[1] + r * math.sin(a), int(rng.integers(0, 2)), 5.0))
    return np.array(rows), Cip, h


def test_the_model_finds_the_entrance_rim_and_the_wall_height():
    cand, Cip, h = _model_steps(clutter=40)
    m = H.fit_hole_model(cand, Cip, h, np.zeros(2), 5.5)
    assert not isinstance(m, str), m
    assert abs(m.d - 12.0) < 0.02
    assert np.allclose(m.centre_xy, [0.3, -0.2], atol=0.02)
    assert abs(m.t - 3.0) < 0.2
    assert m.cover_deg > 340


def test_the_exit_rim_does_not_pull_the_diameter_down():
    # read with the exit rim's steps only on the wall-seen side and the top rim's steps left out there, a circle
    # through all of them would read small; the model keeps them apart
    cand, Cip, h = _model_steps(R=12.5, t=5.0)
    m = H.fit_hole_model(cand, Cip, h, np.zeros(2), 12.0)
    assert not isinstance(m, str), m
    assert abs(m.d - 25.0) < 0.03
    f = H.fit_circle(cand[:, :2])
    assert 25.0 - 2 * f[1] > 0.1  # one circle through everything is pulled small


def test_the_cameras_must_agree_on_the_rim():
    cand, Cip, h = _model_steps()
    cand[cand[:, 2] == 1, :2] *= 1.05  # the right camera's rim 5 % larger
    m = H.fit_hole_model(cand, Cip, h, np.zeros(2), 6.0)
    assert isinstance(m, str) and ("apart" in m or "too little" in m or "too few" in m)


def test_the_plus_minus_keeps_a_shared_part_and_widens_with_little_coverage():
    def fr(d, cover):
        return H.ModelReading(True, "", np.zeros(3), d, float("nan"), cover, 50, 0, 0.0, np.zeros(2))

    many = H.combine_model([fr(10.0 + 0.01 * k, 330.0) for k in range(40)], [np.eye(4)] * 40)
    assert abs(many["pm"] - math.hypot(many["pm_stat"], H.B_SHARED_MM)) < 1e-12
    assert many["pm"] >= H.B_SHARED_MM  # more frames never take it under the shared part
    half = H.combine_model([fr(10.0 + 0.01 * k, 180.0) for k in range(40)], [np.eye(4)] * 40)
    assert abs(half["pm"] / many["pm"] - math.sqrt(270.0 / 180.0)) < 1e-9
    assert "refused" in H.combine_model([fr(10.0, 100.0)] * 10, [np.eye(4)] * 10)


def test_a_frame_without_dots_takes_the_pose_halfway_between_its_neighbours():
    from scipy.spatial.transform import Rotation

    Ta, Tb = np.eye(4), np.eye(4)
    Tb[:3, :3] = Rotation.from_rotvec([0.0, 0.0, 0.2]).as_matrix()
    Tb[:3, 3] = [2.0, 0.0, -4.0]
    T = H.interp_pose(Ta, Tb)
    assert np.allclose(T[:3, 3], [1.0, 0.0, -2.0])
    assert np.allclose(Rotation.from_matrix(T[:3, :3]).as_rotvec(), [0.0, 0.0, 0.1])


# --------------------------------------------------------------------------- step 3
def test_a_hole_brighter_than_the_face_is_seeded_with_step_3_only():
    cx, cy, r = 83.13, -42.07, 5.0
    cams = _cams()
    imgs = [_image(c, cx, cy, r, 0, hole_grey=80.0) for c in cams]  # a lit background, brighter than the face
    depth = np.full(SIZE, Z0, np.float32)
    X = H.depth_points(depth, np.full(SIZE, 0.5, np.float32), K, np.array([cx + r + 1.5, cy, Z0]))
    click = np.array([cx + r + 1.5, cy, Z0])
    old = H.read_frame_model(imgs, cams, X, click)
    assert not old.ok or abs(old.d - 2 * r) > 0.2  # step 2's dark seed misses it, or seeds on something else
    new = H.read_frame_model(imgs, cams, X, click, cfg=H.Step3())
    assert new.ok, new.why
    assert abs(new.d - 2 * r) < 0.1


def test_steps_on_the_face_pair_up_between_the_cameras_and_steps_below_it_do_not():
    rows = [(0, 0, 0, 1.0, 3, 6.00), (0, 0, 1, 1.0, 3, 6.05),  # the rim: the same radius in both
            (0, 0, 0, 1.0, 4, 4.00), (0, 0, 1, 1.0, 4, 5.20),  # a floor feature: the parallax moves it
            (0, 0, 0, 1.0, 5, 6.00)]  # seen by one camera only
    ok = H.stereo_paired(np.array(rows, float))
    assert ok.tolist() == [True, True, False, False, False]


def test_a_known_thickness_holds_the_wall_and_a_clean_hole_is_not_refused_for_two_readings():
    cand, Cip, h = _model_steps(clutter=40)
    cand = np.column_stack([cand, np.zeros(len(cand)), np.zeros(len(cand))])
    # held at a known wall, the fit starts where the hidden-half circle puts it (near, not on, the rim)
    m = H.fit_hole_model(cand[:, :4], Cip, h, np.array([0.25, -0.15]), 5.9, t_known=3.0, two_hyp=True)
    assert not isinstance(m, str), m
    assert m.t == 3.0 and abs(m.d - 12.0) < 0.02
    m = H.fit_hole_model(cand[:, :4], Cip, h, np.zeros(2), 5.5, two_hyp=True)
    assert not isinstance(m, str), m
    assert abs(m.d - 12.0) < 0.02


def test_centres_far_apart_refuse_or_leave_the_position_unsure():
    def fr(d, cx):
        return H.ModelReading(True, "", np.array([cx, 0.0, 0.0]), d, float("nan"), 330.0, 50, 0, 0.0, np.zeros(2))

    xs = np.linspace(-1, 1, 20)
    far = H.combine_model([fr(10.0, 5.0 * x) for x in xs], [np.eye(4)] * 20, centre_rule=True)
    assert "refused" in far and "not one hole" in far["refused"]
    path = H.combine_model([fr(10.0 + 0.001 * k, 2.0 * x) for k, x in enumerate(xs)], [np.eye(4)] * 20, centre_rule=True)
    assert "refused" not in path and path.get("position_unsure")
    rim = H.combine_model([fr(10.0 + 0.8 * x, 2.0 * x) for x in xs], [np.eye(4)] * 20, centre_rule=True)
    assert "refused" in rim and "diameter" in rim["refused"]
    assert "refused" not in H.combine_model([fr(10.0, 4.0 * x) for x in xs], [np.eye(4)] * 20)  # step 2 only warned
