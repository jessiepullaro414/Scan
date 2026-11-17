"""ohs.measure.frame: the part's own coordinate system from fitted features (3-2-1), on a box with holes.

The box is 20 mm, centred at its own origin, with a hole of radius 3 at (3, -2) in its top face; it
is placed at a random rigid pose in the scan's frame, so nothing lines up with the scan's axes by
chance. Every random generator is seeded.

* Exact fits: the frame from (top face, side face, hole) puts the hole's centre at the origin, the
  top face at Z = 0 with its normal along Z, the side face's normal along X, to 1e-9; Z and X point
  into the part. A mesh turned into the frame, saved and loaded, has the hole at the origin.
* Every combination offered gives a proper rotation; the sign of a fit's normal or axis, and dict or
  dataclass input, change nothing.
* Round trips: points, directions, fits (dataclasses and dicts) and the record itself.
* Refusals with a message a user can act on.
* Noisy fits from points (sigma per point, as in ``test_measure_fit``), 200 trials per case: ±2σ
  of the frame's whole angle, its turn about Z and its origin covers the true error 90-100 % of
  the time, and the mean of ``(error / σ)^2`` is about 1 (0.7-1.35).
"""
from __future__ import annotations

import json
import math
from dataclasses import replace

import numpy as np
import pytest

from mesh_shapes import box
from ohs.measure.fit import CircleFit, CylinderFit, PlaneFit, fit_circle, fit_plane
from ohs.measure.frame import PartFrame, part_frame
from ohs.track.export import load_mesh, save_mesh

HALF = 10.0  # the box's half size, mm
HOLE = np.array([3.0, -2.0, HALF])  # the hole's centre (radius 3) in the box's own frame
HOLE_R = 3.0
TRIALS = 200


def pose(seed: int) -> tuple[np.ndarray, np.ndarray]:
    """A random rotation (det +1) and translation: the box's place in the scan's frame."""
    rng = np.random.default_rng(seed)
    Q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    if np.linalg.det(Q) < 0:
        Q[:, 0] = -Q[:, 0]
    return Q, rng.uniform(-200.0, 200.0, 3)


def place(Q, t, X) -> np.ndarray:
    return np.asarray(X, dtype=np.float64) @ Q.T + t


def turn(Q, d) -> np.ndarray:
    return np.asarray(d, dtype=np.float64) @ Q.T


def features(Q, t, s: float = 0.01) -> dict:
    """The box's features as exact fits at the pose ``(Q, t)``, each ± ``s`` (degrees or mm)."""
    P = lambda x: place(Q, t, x)  # noqa: E731
    D = lambda d: turn(Q, d)  # noqa: E731
    return {
        "top": PlaneFit(P([0, 0, HALF]), D([0, 0, 1]), 0.0, 0.0, 400, s, s),
        "bottom": PlaneFit(P([0, 0, -HALF]), D([0, 0, 1]), 0.0, 0.0, 400, s, s),
        "side": PlaneFit(P([HALF, 0, 0]), D([1, 0, 0]), 0.0, 0.0, 400, s, s),
        "front": PlaneFit(P([0, -HALF, 0]), D([0, -1, 0]), 0.0, 0.0, 400, s, s),
        "hole": CircleFit(P(HOLE), D([0, 0, 1]), HOLE_R, 0.0, 60, s, s),
        "hole2": CircleFit(P([-5, 4, HALF]), D([0, 0, 1]), 2.0, 0.0, 60, s, s),
        "bore": CylinderFit(P([3, -2, 5]), D([0, 0, 1]), HOLE_R, 0.0, 200, s, s, s),  # the hole's wall
        "bore2": CylinderFit(P([-5, 4, 5]), D([0, 0, 1]), 2.0, 0.0, 200, s, s, s),  # hole2's wall
        "cross": CylinderFit(P([0, 4, 3]), D([1, 0, 0]), 2.0, 0.0, 200, s, s, s),  # a bore through the side
        "side_hole": CircleFit(P([HALF, 4, 3]), D([1, 0, 0]), 2.0, 0.0, 60, s, s),  # its end in the side face
        "lying": CylinderFit(P([0, 4, HALF]), D([1, 0, 0]), 2.0, 0.0, 200, s, s, s),  # lies in the top face
        "point": {"kind": "point", "point": [float(x) for x in P([-8, -8, HALF])], "sigma_mm": s},
    }


def flipped(f):
    """``f`` with its normal or axis the other way round (dataclass or dict)."""
    if isinstance(f, dict):
        g = dict(f)
        for k in ("normal", "axis"):
            if k in g:
                g[k] = [-x for x in g[k]]
        return g
    name = "axis" if isinstance(f, CylinderFit) else "normal"
    return replace(f, **{name: -getattr(f, name)})


def angle_deg(a, b) -> float:
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(a, b))), float(np.dot(a, b))))


def proper(M) -> None:
    M = np.asarray(M)
    R = M[:3, :3]
    assert M.shape == (4, 4) and np.allclose(M[3], [0, 0, 0, 1], atol=0)
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-12) and np.linalg.det(R) == pytest.approx(1.0, abs=1e-12)


# --------------------------------------------------------------------------- exact fits
def test_box_frame_from_top_side_and_hole_is_exact():
    Q, t = pose(1)
    f = features(Q, t, 0.0)
    fr = part_frame(f["top"], f["side"], f["hole"])
    proper(fr.matrix)
    assert np.allclose(fr.points_to_part(f["hole"].center), 0.0, atol=1e-9)  # the hole's centre is the origin
    top, side, hole = (fr.fit_to_part(f[k]) for k in ("top", "side", "hole"))
    assert np.allclose(np.abs(top.normal), [0, 0, 1], atol=1e-12) and abs(top.point[2]) < 1e-9  # Z = 0 on the top
    assert np.allclose(np.abs(side.normal), [1, 0, 0], atol=1e-12)
    assert side.point[0] == pytest.approx(-(HALF - HOLE[0]), abs=1e-9)  # the side face 7 mm from the hole
    assert np.allclose(np.abs(hole.normal), [0, 0, 1], atol=1e-12)
    # Z and X point into the part: the box's centre lies at +X and +Z
    centre = fr.points_to_part(place(Q, t, [0, 0, 0]))
    assert centre == pytest.approx([HOLE[0], -HOLE[1], HALF], abs=1e-9)
    # what it was built from, and which way the axes are named
    assert [b["role"] for b in fr.built_from] == ["primary", "secondary", "origin"]
    assert [b["kind"] for b in fr.built_from] == ["plane", "plane", "circle"]
    assert fr.axes == ("Z", "X", "Y") and not fr.warnings


def test_axis_names_are_a_parameter():
    Q, t = pose(2)
    f = features(Q, t, 0.0)
    fr = part_frame(f["top"], f["side"], f["hole"], axes="XY")  # the top's normal is X, the side's Y
    proper(fr.matrix)
    assert fr.axes == ("X", "Y", "Z")
    assert np.allclose(np.abs(fr.fit_to_part(f["top"]).normal), [1, 0, 0], atol=1e-12)
    assert np.allclose(np.abs(fr.fit_to_part(f["side"]).normal), [0, 1, 0], atol=1e-12)
    assert np.allclose(fr.points_to_part(f["hole"].center), 0.0, atol=1e-9)


def test_flip_reverses_an_axis_and_keeps_the_frame_right_handed():
    Q, t = pose(3)
    f = features(Q, t, 0.0)
    base = part_frame(f["top"], f["side"], f["hole"])
    up = part_frame(f["top"], f["side"], f["hole"], flip_primary=True)
    proper(up.matrix)
    R0, R1 = base.matrix[:3, :3], up.matrix[:3, :3]
    assert np.allclose(R1[2], -R0[2]) and np.allclose(R1[0], R0[0]) and np.allclose(R1[1], -R0[1])
    both = part_frame(f["top"], f["side"], f["hole"], flip_primary=True, flip_secondary=True)
    proper(both.matrix)
    assert np.allclose(both.matrix[:3, :3][0], -R0[0]) and np.allclose(both.matrix[:3, :3][1], R0[1])


def test_three_planes_meet_at_the_corner():
    Q, t = pose(4)
    f = features(Q, t, 0.0)
    fr = part_frame(f["top"], f["side"], f["front"])
    proper(fr.matrix)
    assert np.allclose(fr.points_to_part(place(Q, t, [HALF, -HALF, HALF])), 0.0, atol=1e-9)


def test_strict_321_lets_the_secondary_set_its_own_zero():
    """With ``secondary_zero`` the side face is X = 0 and the hole sets only Y."""
    Q, t = pose(5)
    f = features(Q, t, 0.0)
    fr = part_frame(f["top"], f["side"], f["hole"], secondary_zero=True)
    proper(fr.matrix)
    assert abs(fr.fit_to_part(f["side"]).point[0]) < 1e-9
    assert fr.points_to_part(f["hole"].center) == pytest.approx([HALF - HOLE[0], 0.0, 0.0], abs=1e-9)


def test_a_second_hole_across_the_primary_sets_the_turn():
    """Plane, hole, hole: X runs from the origin hole to the second one."""
    Q, t = pose(6)
    f = features(Q, t, 0.0)
    for second in ("hole2", "bore2"):
        fr = part_frame(f["top"], f[second], f["hole"])
        proper(fr.matrix)
        p = fr.points_to_part(place(Q, t, [-5, 4, HALF]))
        assert p[0] == pytest.approx(math.hypot(8.0, 6.0), abs=1e-9) and abs(p[1]) < 1e-9 and abs(p[2]) < 1e-9


SECONDARIES = ["side", "cross", "bore2", "hole2", "side_hole"]
ORIGINS = ["hole", "bore", "front", "point"]
AXES = ["ZX", "ZY", "XY", "XZ", "YZ", "YX"]
# a plane as the origin needs a secondary that leaves one coordinate free across it: only the side face does
INVALID = {(s, "front") for s in SECONDARIES if s != "side"}


@pytest.mark.parametrize("axes", AXES)
@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize("secondary", SECONDARIES)
def test_every_combination_is_a_proper_rotation_whatever_the_signs(secondary, origin, axes):
    Q, t = pose(7)
    f = features(Q, t)
    if (secondary, origin) in INVALID:
        with pytest.raises(ValueError):
            part_frame(f["top"], f[secondary], f[origin], axes=axes)
        return
    fr = part_frame(f["top"], f[secondary], f[origin], axes=axes)
    proper(fr.matrix)
    # the origin lies on the primary plane
    i = "XYZ".index(axes[0])
    assert abs(fr.fit_to_part(f["top"]).point[i]) < 1e-9
    # the same frame from fits whose normals and axes point the other way, singly or all together,
    # and from their plain dicts
    keys = ("top", secondary, origin)
    for k in range(4):
        args = [flipped(f[n]) if k in (j, 3) else f[n] for j, n in enumerate(keys)]
        assert np.allclose(part_frame(*args, axes=axes).matrix, fr.matrix, atol=1e-12), k
    plain = [f[n] if isinstance(f[n], dict) else f[n].to_dict() for n in keys]
    assert np.allclose(part_frame(*plain, axes=axes).matrix, fr.matrix, atol=1e-12)


def test_signs_are_the_same_at_every_pose():
    """The same features give the same frame in the part's own terms, wherever the part lies."""
    got = []
    for seed in range(5):
        Q, t = pose(100 + seed)
        f = features(Q, t, 0.0)
        fr = part_frame(f["top"], f["side"], f["hole"])
        got.append(fr.points_to_part(place(Q, t, [[0, 0, 0], [-HALF, HALF, -HALF]])))
    assert all(np.allclose(g, got[0], atol=1e-9) for g in got)


# --------------------------------------------------------------------------- round trips
def test_points_directions_and_fits_go_into_the_frame_and_back():
    Q, t = pose(8)
    f = features(Q, t)
    fr = part_frame(f["top"], f["side"], f["hole"])
    rng = np.random.default_rng(9)
    X = rng.uniform(-50, 50, (100, 3))
    assert np.allclose(fr.points_from_part(fr.points_to_part(X)), X, atol=1e-12)
    D = rng.normal(size=(100, 3))
    assert np.allclose(fr.directions_from_part(fr.directions_to_part(D)), D, atol=1e-12)
    assert np.allclose(np.linalg.norm(fr.directions_to_part(D), axis=1), np.linalg.norm(D, axis=1))
    for name in ("top", "cross", "hole", "point"):
        fit = f[name]
        if name == "point":
            assert close(fr.fit_from_part(fr.fit_to_part(fit)), fit)
            continue
        back = fr.fit_from_part(fr.fit_to_part(fit))
        assert type(back) is type(fit) and close(back.to_dict(), fit.to_dict())
        # a plain dict (as saved) and a region's or a kept feature's wrapping of it
        d = fit.to_dict()
        assert close(fr.fit_to_part(d), fr.fit_to_part(fit).to_dict())
        wrapped = {"id": "f1", "kind": "hole", "fit": d, "warnings": []}
        into = fr.fit_to_part(wrapped)
        assert into["id"] == "f1" and close(into["fit"], fr.fit_to_part(fit).to_dict())
        assert close(fr.fit_from_part(into)["fit"], d)
        assert wrapped["fit"] is d and d == fit.to_dict()  # the input is left alone
    # the lengths, radii and ± values do not change; positions and directions do
    top = fr.fit_to_part(f["top"])
    assert top.normal_sigma_deg == f["top"].normal_sigma_deg and not np.allclose(top.point, f["top"].point)


def close(a, b) -> bool:
    """Equal dicts of numbers, lists of numbers and strings, numbers to 1e-9."""
    if isinstance(a, dict):
        return isinstance(b, dict) and set(a) == set(b) and all(close(a[k], b[k]) for k in a)
    if isinstance(a, str) or a is None:
        return a == b
    return bool(np.allclose(np.asarray(a, float), np.asarray(b, float), atol=1e-9, rtol=0))


def test_record_to_dict_and_back():
    Q, t = pose(10)
    f = features(Q, t)
    kept = {"id": "f7", "name": "Top", "kind": "plane", "fit": f["top"].to_dict()}
    fr = part_frame(kept, f["side"], f["hole"], ids=(None, "f2", "f3"), flip_secondary=True)
    d = fr.to_dict()
    assert json.loads(json.dumps(d)) == d
    assert d["format_version"] == 1 and d["units"] == "mm"
    assert len(d["matrix"]) == 4 and all(len(r) == 4 for r in d["matrix"])
    assert d["axes"] == {"primary": "Z", "secondary": "X", "third": "Y"}
    assert [b["id"] for b in d["built_from"]] == ["f7", "f2", "f3"]
    assert [b["kind"] for b in d["built_from"]] == ["plane", "plane", "circle"]
    assert all(b["sets"] for b in d["built_from"])
    assert set(d["sigma"]) == {"angle_deg", "tilt_deg", "turn_deg", "origin_mm", "origin_axes_mm"}
    back = PartFrame.from_dict(json.loads(json.dumps(d)))
    assert np.array_equal(back.matrix, fr.matrix) and back.to_dict() == d
    with pytest.raises(ValueError, match="format_version"):
        PartFrame.from_dict({**d, "format_version": 99})
    bad = json.loads(json.dumps(d))
    bad["matrix"][0][0] = 2.0
    with pytest.raises(ValueError, match="rotation"):
        PartFrame.from_dict(bad)


def ring_box(Q, t):
    """The box (mesh_shapes.box, 20 mm) with its top face replaced by one with an exact round hole,
    placed at ``(Q, t)``. Returns ``(V, F, N, top vertex ids, side vertex ids, ring vertex ids)``."""
    from scipy.spatial import Delaunay

    V, F = box(2 * HALF, 32)
    keep = ~np.all(V[F][:, :, 2] > HALF - 1e-9, axis=1)
    F = F[keep]
    s = np.linspace(-HALF, HALF, 33)
    X, Y = np.meshgrid(s, s)
    pts = np.c_[X.ravel(), Y.ravel()]
    pts = pts[np.hypot(pts[:, 0] - HOLE[0], pts[:, 1] - HOLE[1]) > HOLE_R + 0.4]
    a = 2 * math.pi * np.arange(48) / 48
    ring = np.arange(len(pts), len(pts) + 48)
    pts = np.vstack([pts, np.c_[HOLE[0] + HOLE_R * np.cos(a), HOLE[1] + HOLE_R * np.sin(a)]])
    tri = Delaunay(pts).simplices
    cen = pts[tri].mean(axis=1)
    tri = tri[np.hypot(cen[:, 0] - HOLE[0], cen[:, 1] - HOLE[1]) > HOLE_R]
    top = np.arange(len(V), len(V) + len(pts))
    V = np.vstack([V, np.c_[pts, np.full(len(pts), HALF)]])
    F = np.vstack([F, tri + top[0]])
    N = np.zeros_like(V)
    N[:, 2] = 1.0  # a stand-in normal per vertex: enough to see them turned
    side = np.flatnonzero(np.abs(V[:, 0] - HALF) < 1e-9)
    return place(Q, t, V), F, turn(Q, N), top, side, top[0] + ring


def test_a_mesh_turned_into_the_frame_and_saved_has_the_hole_at_the_origin(tmp_path):
    Q, t = pose(11)
    V, F, N, top, side, ring = ring_box(Q, t)
    fr = part_frame(fit_plane(V[top]), fit_plane(V[side]), fit_circle(V[ring]))
    Vp, Np = fr.mesh_to_part(V, N)
    assert np.allclose(Np, fr.directions_to_part(N)) and np.allclose(Vp, fr.points_to_part(V))
    for ext in (".ply", ".obj"):
        path = save_mesh(tmp_path / f"part{ext}", Vp, F, Np)
        Vl, Fl, Nl = load_mesh(path)
        assert np.array_equal(Fl, F) and np.allclose(Vl, Vp, atol=1e-5)  # float32 in the file
        hole = fit_circle(Vl[ring])
        assert np.linalg.norm(hole.center) < 1e-4  # the hole at the origin, to the file's precision
        assert hole.radius_mm == pytest.approx(HOLE_R, abs=1e-4)
        assert abs(fit_plane(Vl[top]).point[2]) < 1e-4 and angle_deg(np.abs(fit_plane(Vl[top]).normal), [0, 0, 1]) < 1e-3
        if Nl is not None:
            assert np.allclose(np.abs(Nl[top]), [0, 0, 1], atol=1e-5)


# --------------------------------------------------------------------------- refusals
def _refusals():
    Q, t = pose(12)
    f = features(Q, t)
    tilted = replace(f["side"], normal=turn(Q, [math.sin(math.radians(5)), 0, math.cos(math.radians(5))]))
    return [
        ("a cylinder as the primary", lambda: part_frame(f["bore"], f["side"], f["hole"]), "primary must be a plane"),
        ("a point as the secondary", lambda: part_frame(f["top"], f["point"], f["hole"]),
         "secondary must be a plane, a cylinder or a hole"),
        ("an unknown kind", lambda: part_frame(f["top"], f["side"], {"kind": "sphere", "center": [0, 0, 0]}),
         "origin must be"),
        ("two parallel faces", lambda: part_frame(f["top"], f["bottom"], f["hole"]), "nearly parallel to the primary"),
        ("a face 5° from the primary", lambda: part_frame(f["top"], tilted, f["hole"]), "nearly parallel to the primary"),
        ("a hole across the primary, a plane as the origin", lambda: part_frame(f["top"], f["hole2"], f["front"]),
         "with a hole, a cylinder or a point as the origin"),
        ("the origin hole as the secondary too", lambda: part_frame(f["top"], f["hole"], f["hole"]),
         "too close to the origin"),
        ("the hole's own wall as the secondary", lambda: part_frame(f["top"], f["bore"], f["hole"]),
         "too close to the origin"),
        ("an origin cylinder lying in the primary plane", lambda: part_frame(f["top"], f["side"], f["lying"]),
         "does not cross the primary plane"),
        ("a third plane parallel to the secondary", lambda: part_frame(f["top"], f["side"], f["side"]),
         "does not fix the origin"),
        ("a line along the third plane's normal", lambda: part_frame(f["top"], f["cross"], f["front"]),
         "does not fix the origin"),
        ("secondary_zero with a hole as the secondary",
         lambda: part_frame(f["top"], f["hole2"], f["hole"], secondary_zero=True), "secondary_zero"),
        ("one axis twice", lambda: part_frame(f["top"], f["side"], f["hole"], axes="ZZ"), "axes must be"),
        ("a bad axis", lambda: part_frame(f["top"], f["side"], f["hole"], axes="ZW"), "axes must be"),
        ("a NaN normal", lambda: part_frame(replace(f["top"], normal=np.array([np.nan, 0, 1])), f["side"], f["hole"]),
         "finite"),
    ]


@pytest.mark.parametrize("name, call, message", _refusals(), ids=[c[0] for c in _refusals()])
def test_refusals_say_what_to_do(name, call, message):
    with pytest.raises(ValueError, match=message):
        call()


def test_a_sign_with_nothing_to_go_on_is_warned_about():
    """Plane, hole, hole all in one face: nothing tells which side the part is on."""
    Q, t = pose(13)
    f = features(Q, t)
    fr = part_frame(f["top"], f["hole2"], f["hole"])
    assert any("side" in w for w in fr.warnings)
    proper(fr.matrix)
    # with the hole's wall as the origin, the bore's middle lies in the part: no warning
    fr = part_frame(f["top"], f["hole2"], f["bore"])
    assert not fr.warnings and fr.points_to_part(place(Q, t, [0, 0, 0]))[2] > 0


def test_a_hole_direction_has_no_sigma_and_says_so():
    Q, t = pose(14)
    f = features(Q, t)
    fr = part_frame(f["top"], f["side_hole"], f["hole"])
    assert fr.sigma["turn_deg"] is None and fr.sigma["angle_deg"] is None
    assert fr.sigma["tilt_deg"] is not None and fr.sigma["origin_mm"] is not None
    assert any("±" in w for w in fr.warnings)
    # a saved fit without its ± fields, a point without sigma_mm: the values they reach are unknown
    bare = {k: v for k, v in f["top"].to_dict().items() if "sigma" not in k}
    fr = part_frame(bare, f["side"], {"kind": "point", "point": f["point"]["point"]})
    assert fr.sigma["angle_deg"] is None and fr.sigma["tilt_deg"] is None and fr.sigma["origin_mm"] is None
    assert fr.sigma["turn_deg"] is not None  # the side face alone sets the turn: its ± is known
    assert any("primary plane has no ±" in w for w in fr.warnings)
    assert any("origin point has no ±" in w for w in fr.warnings)


# --------------------------------------------------------------------------- the ± values
def face_points(rng, centre, normal, n=150, size=2 * HALF):
    """``n`` points on a ``size`` square of the plane, noise of a per-point sigma 0.02-0.1 mm, and the sigmas."""
    normal = np.asarray(normal, float)
    u = np.cross(normal, [1.0, 0, 0] if abs(normal[0]) < 0.9 else [0, 1.0, 0])
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    a, b = rng.uniform(-size / 2, size / 2, (2, n))
    s = rng.uniform(0.02, 0.1, n)
    P = np.asarray(centre) + a[:, None] * u + b[:, None] * v + rng.normal(size=(n, 3)) * s[:, None]
    return P, s


def edge_points(rng, centre, r, n=60):
    a = rng.uniform(0, 2 * math.pi, n)
    s = rng.uniform(0.02, 0.1, n)
    P = np.asarray(centre) + r * np.c_[np.cos(a), np.sin(a), np.zeros(n)] + rng.normal(size=(n, 3)) * s[:, None]
    return P, s


def noisy_fits(rng, Q, t) -> dict:
    """The box's top, side and front faces and both holes fitted to noisy points, at the pose."""
    out = {}
    for name, centre, normal in (("top", [0, 0, HALF], [0, 0, 1]), ("side", [HALF, 0, 0], [1, 0, 0]),
                                 ("front", [0, -HALF, 0], [0, -1, 0])):
        P, s = face_points(rng, centre, normal)
        out[name] = fit_plane(place(Q, t, P), s)
    for name, centre, r in (("hole", HOLE, HOLE_R), ("hole2", [-5.0, 4.0, HALF], 2.0)):
        P, s = edge_points(rng, centre, r)
        out[name] = fit_circle(place(Q, t, P), s)
    return out


def rotation_angle_deg(R) -> float:
    return math.degrees(math.acos(min(1.0, max(-1.0, (np.trace(R) - 1.0) / 2.0))))


CASES = {  # (secondary, origin, options)
    "top, side, hole": ("side", "hole", {}),
    "top, hole2, hole (plane, hole, hole)": ("hole2", "hole", {}),
    "top, side, front (corner)": ("side", "front", {}),
    "top, side, hole, strict 3-2-1": ("side", "hole", {"secondary_zero": True}),
}


@pytest.mark.parametrize("case", list(CASES))
def test_two_sigma_covers_the_true_frame_about_95_percent_of_the_time(case):
    secondary, origin, options = CASES[case]
    rng = np.random.default_rng(500 + list(CASES).index(case))
    err = {"angle": [], "turn": [], "origin": []}
    sig = {"angle": [], "turn": [], "origin": []}
    for k in range(TRIALS):
        Q, t = pose(10_000 + k)
        exact = features(Q, t)
        truth = part_frame(exact["top"], exact[secondary], exact[origin], **options)
        f = noisy_fits(rng, Q, t)
        fr = part_frame(f["top"], f[secondary], f[origin], **options)
        dR = fr.matrix[:3, :3] @ truth.matrix[:3, :3].T
        err["angle"].append(rotation_angle_deg(dR))
        sig["angle"].append(fr.sigma["angle_deg"])
        err["turn"].append(abs(math.degrees(dR[1, 0])))  # about Z, the first axis (small angles)
        sig["turn"].append(fr.sigma["turn_deg"])
        # where the true origin lies in the measured frame
        err["origin"].append(float(np.linalg.norm(fr.points_to_part(truth.points_from_part([0.0, 0.0, 0.0])))))
        sig["origin"].append(fr.sigma["origin_mm"])
    for q in ("angle", "turn", "origin"):
        z = np.asarray(err[q]) / np.asarray(sig[q])
        cover, z2 = float(np.mean(z <= 2.0)), float(np.mean(z * z))
        assert 0.90 <= cover <= 1.0 and 0.7 <= z2 <= 1.35, f"{case}: {q}: ±2σ covers {cover:.1%}, mean (err/σ)^2 {z2:.2f}"
