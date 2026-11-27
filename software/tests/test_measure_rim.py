"""``ohs.measure.rim``: a hole read from the camera frames at a click, on synthetic images of known circles (a dotted
face, dark holes, a blurred edge drawn analytically; no rendering, no scan). ENGINE_PLAN X15."""
from __future__ import annotations

import math

import numpy as np

from ohs.measure import rim

K = np.array([[720.0, 0.0, 640.0], [0.0, 720.0, 400.0], [0.0, 0.0, 1.0]])
Z0 = 250.0  # the face: the plane z = Z0 in frame C (= the world here), facing the cameras
SIZE = (800, 1280)
CAL = {"K1": K.tolist(), "D1": [0.0] * 5, "K2": K.tolist(), "D2": [0.0] * 5, "R": np.eye(3).tolist(),
       "T": [-60.0, 0.0, 0.0], "R1": np.eye(3).tolist(), "P1": np.column_stack([K, np.zeros(3)]).tolist(),
       "image_size": [1280, 800]}
NORMAL = np.array([0.0, 0.0, -1.0])


def _cams():
    return rim.stereo_cameras(CAL)


def _shade(X, Y, holes, dots):
    """The face: 30 grey with projector dots on a jittered 3.5 mm grid (``dots`` seeds the jitter: the dots are fixed
    to the scanner, so each frame has them elsewhere); the holes ``(x, y, r)``: 10 grey (a dark background)."""
    g = 3.5
    i, j = np.floor(X / g), np.floor(Y / g)
    rng = np.random.default_rng(dots)
    jit = rng.uniform(0.25, 0.75, size=(64, 64, 2))
    dx = (i + jit[i.astype(int) % 64, j.astype(int) % 64, 0]) * g - X
    dy = (j + jit[i.astype(int) % 64, j.astype(int) % 64, 1]) * g - Y
    out = 30.0 + 120.0 * np.exp(-(dx * dx + dy * dy) / (2 * 0.3 ** 2))
    for cx, cy, r in holes:
        out = np.where((X - cx) ** 2 + (Y - cy) ** 2 < r * r, 10.0, out)
    return out


def _image(cam, holes, dots, ss=4):
    """The camera's image of the face (supersampled ``ss`` x ``ss`` a pixel, then a 0.6 px blur) in a window round
    the first hole; constant face grey elsewhere."""
    import cv2

    img = np.full(SIZE, 30.0, np.float32)
    C = cam.centre
    cx, cy = holes[0][:2]
    uc, vc = (K @ (cam.R @ np.array([cx, cy, Z0]) + cam.t))[:2] / Z0
    half = int(40.0 * K[0, 0] / Z0)
    u0, v0 = int(uc) - half, int(vc) - half
    n = 2 * half
    o = (np.arange(n * ss) + 0.5) / ss - 0.5
    uu, vv = np.meshgrid(u0 + o, v0 + o)
    d = np.stack([(uu - K[0, 2]) / K[0, 0], (vv - K[1, 2]) / K[1, 1], np.ones_like(uu)], -1) @ cam.R
    s = (Z0 - C[2]) / d[..., 2]
    X, Y = C[0] + s * d[..., 0], C[1] + s * d[..., 1]
    img[v0:v0 + n, u0:u0 + n] = _shade(X, Y, holes, dots).reshape(n, ss, n, ss).mean(axis=(1, 3)).astype(np.float32)
    return cv2.GaussianBlur(img, (0, 0), 0.6)


def _read(holes, click, n=5, shift=None):
    """The reading over ``n`` frames, each with the dots elsewhere, all from one camera position. ``shift`` (4x4)
    moves the whole scan (poses and click) in the world: what the reading sees in its frames is unchanged."""
    cams = _cams()
    frames = {i: tuple(_image(c, holes, i) for c in cams) for i in range(n)}
    z = np.full(SIZE, Z0, np.float32)  # flat everywhere: as if the matcher had filled the holes
    s = np.full(SIZE, 0.5, np.float32)
    T = np.eye(4) if shift is None else shift
    poses = {i: T for i in range(n)}
    click_w = T[:3, :3] @ np.asarray(click, float) + T[:3, 3]
    return rim.read_hole(click_w, T[:3, :3] @ NORMAL, poses=poses, calib=CAL, images=frames.get,
                         depth=lambda i: (z, s), frames=list(range(n)))


def test_a_known_circle_is_read_within_a_twentieth_of_a_millimetre():
    cx, cy, r = 83.13, -42.07, 5.0  # off axis (the cameras outside its footprint), off the pixel grid
    res = _read([(cx, cy, r)], [cx + r + 1.5, cy, Z0])
    assert res.refused is None, res.refused
    assert abs(res.diameter_mm - 2 * r) < 0.05, res.diameter_mm
    assert np.linalg.norm(res.centre - [cx, cy, Z0]) < 0.05
    assert res.frames_used == 5 and res.pm_mm <= rim.PM_LIMIT_MM
    d = res.to_dict()
    assert d["diameter_mm"] == res.diameter_mm and "refused" not in d


def test_a_sub_pixel_move_of_the_hole_is_followed():
    a = _read([(83.13, -42.07, 5.0)], [89.63, -42.07, Z0])
    b = _read([(83.23, -42.07, 5.0)], [89.73, -42.07, Z0])  # 0.1 mm is 0.29 px here
    assert abs((b.centre[0] - a.centre[0]) - 0.1) < 0.03
    assert abs(b.centre[1] - a.centre[1]) < 0.03
    assert abs(b.diameter_mm - a.diameter_mm) < 0.03


def test_too_few_frames_refuse_in_words():
    res = _read([(83.13, -42.07, 5.0)], [89.63, -42.07, Z0], n=3)
    assert res.refused == "only 3 frames show the rim (at least 5 needed)"
    assert res.to_dict() == {"refused": res.refused, "frames_used": 3, "frames": 3}


def _frame(d, centre=(0.0, 0.0, 0.0), cover=330.0):
    return rim.FrameReading(True, "", np.asarray(centre, float), d, float("nan"), cover, 50, 0, 0.0,
                            np.zeros(2), np.array([0.0, 0.0, 1.0]))


def test_a_plus_minus_above_its_limit_refuses_in_words():
    ds = [10.0, 10.5, 9.5, 10.6, 9.4, 10.3, 9.7]  # they scatter, though less than the 1 mm that refuses outright
    res = rim.combine_frames([_frame(d) for d in ds], [np.eye(4)] * len(ds), np.array([6.5, 0.0, 0.0]))
    assert res.pm_mm > rim.PM_LIMIT_MM
    assert res.refused == f"the frames do not agree well enough on this rim: ± {res.pm_mm:.2f} mm"


def test_a_click_5_mm_outside_the_read_circle_is_another_holes():
    fr = [_frame(10.0) for _ in range(6)]
    assert rim.combine_frames(fr, [np.eye(4)] * 6, np.array([6.5, 0.0, 0.0])).refused is None
    res = rim.combine_frames(fr, [np.eye(4)] * 6, np.array([10.0, 0.0, 0.0]))
    assert res.refused == "the rim read in the images is 5 mm from the click: it is another hole's"


def test_a_click_beside_a_neighbour_reads_its_own_hole():
    big, small = (83.13, -42.07, 5.0), (94.13, -42.07, 3.0)  # 3 mm rim to rim
    res = _read([small, big], [94.13 - 3.75, -42.07, Z0])  # 0.75 mm outside the Ø6, 2.25 mm from the Ø10
    assert res.refused is None, res.refused
    assert abs(res.diameter_mm - 6.0) < 0.1, res.diameter_mm
    assert np.linalg.norm(res.centre[:2] - small[:2]) < 0.1


def test_the_diameter_does_not_follow_the_pose():
    a = _read([(83.13, -42.07, 5.0)], [89.63, -42.07, Z0])
    T = np.eye(4)
    c, s = math.cos(0.3), math.sin(0.3)
    T[:3, :3] = [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]
    T[:3, 3] = [40.0, -25.0, 7.0]
    b = _read([(83.13, -42.07, 5.0)], [89.63, -42.07, Z0], shift=T)
    assert b.diameter_mm == a.diameter_mm
    assert np.allclose(b.centre, T[:3, :3] @ a.centre + T[:3, 3])


# --------------------------------------------------------------------------- in words (what the app shows)
def test_an_answer_in_words_says_which_diameter_and_never_that_the_path_moves_it():
    res = rim.RimReading(None, diameter_mm=10.0412, pm_mm=0.1049, centre=np.zeros(3), frames_used=17, frames=20)
    w = rim.reading_words(res)
    assert w[0] == "Ø 10.04 mm ± 0.10 from the camera images (the rim where the face ends, read in 17 frames)."
    assert w[1] == rim.WHICH_DIAMETER and "not the bore" in w[1] and "chamfer" in w[1]
    assert len(w) == 2
    res.position_unsure, res.centre_scatter = True, 1.64
    res.warnings = ["the frames put the hole's centre 1.6 mm apart: ...", rim.PATH_WARNS]
    w = rim.reading_words(res)
    assert len(w) == 3 and w[2] == ("The hole's position may be off (the scan's camera path check warns and the "
                                    "camera frames place its centre 1.6 mm apart); its diameter does not depend on "
                                    "the camera path.")


def test_every_refusal_has_a_hint_that_says_what_to_do():
    assert "beside the hole" in rim.refusal_hint("only 2 frames show the rim (at least 5 needed)")
    assert "several sides" in rim.refusal_hint("the top rim is found on only 80 degrees of the round")
    assert "hole you mean" in rim.refusal_hint("the rim read in the images is 9 mm from the click: it is another hole's")
    assert "not between two holes" in rim.refusal_hint(
        "the frames put the hole's centre 4.1 mm apart: this is not one hole's rim")
    for r in ("the frames disagree on the diameter by 1.3 mm", "the frames do not agree well enough on this rim: ± 0.31 mm",
              "the frames put the hole's centre 1.4 mm apart and its diameter 0.41 mm apart: the rim is not read the "
              "same in every frame"):
        assert rim.refusal_hint(r) == rim.HINT_DEFAULT and "calipers" in rim.HINT_DEFAULT
