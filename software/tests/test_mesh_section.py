"""``ohs.mesh.section`` (FEATURE_PLAN I1): the mesh cut by a plane, as the 2D profile.

Known shapes give known profiles: a box a rectangle, a corner a triangle, a cylinder cut at an
angle an ellipse, a sphere a circle, a torus two loops, an open sheet an open line. The awkward
cases are cut on purpose: a plane through vertices and along edges of a regular grid, faces lying
in the plane, a fin of three faces on one edge, a random soup of triangles. The same cut in
another pose gives the same profile; with a part frame the drawing's axes are the part's.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mesh_shapes import grid, icosphere, punch  # noqa: E402

from ohs.measure.fit import PlaneFit  # noqa: E402
from ohs.measure.frame import PartFrame  # noqa: E402
from ohs.mesh import section as sec  # noqa: E402


# --------------------------------------------------------------------------- shapes
def voxels(mask: np.ndarray, step: float = 1.0, origin=(0.0, 0.0, 0.0)) -> tuple[np.ndarray, np.ndarray]:
    """The closed surface of the filled voxels of ``mask`` (a unit-square grid of faces, outward
    normals, vertices shared). Every vertex sits on the grid, so grid planes go exactly through
    vertices and along edges."""
    m = np.pad(np.asarray(mask, bool), 1)
    quads = []
    for a in range(3):
        b, c = (a + 1) % 3, (a + 2) % 3  # e_b x e_c = e_a
        for sign in (1, -1):
            nb = np.roll(m, -sign, axis=a)
            idx = np.argwhere(m & ~nb)
            for i in idx:
                base = i.astype(float) - 1.0
                if sign > 0:
                    base[a] += 1.0
                corners = []
                for db, dc in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    p = base.copy()
                    p[b] += db
                    p[c] += dc
                    corners.append(p)
                if sign < 0:
                    corners = corners[::-1]
                quads.append(corners)
    P = np.asarray(quads).reshape(-1, 3)
    key = np.round(P).astype(np.int64)
    uk, inv = np.unique(key, axis=0, return_inverse=True)
    q = inv.reshape(-1, 4)
    F = np.concatenate([q[:, [0, 1, 2]], q[:, [0, 2, 3]]])
    return uk * step + np.asarray(origin, float), F


def cylinder(r: float = 10.0, h: float = 100.0, n: int = 512, rings: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """A closed cylinder around Z from z = 0 to h, capped, outward normals."""
    t = 2 * np.pi * np.arange(n) / n
    zs = np.linspace(0.0, h, rings + 1)
    V = np.concatenate([np.stack([r * np.cos(t), r * np.sin(t), np.full(n, z)], 1) for z in zs])
    V = np.vstack([V, [[0, 0, 0], [0, 0, h]]])
    F = []
    for k in range(rings):
        i = np.arange(n)
        a, b = k * n + i, k * n + (i + 1) % n
        c, d = a + n, b + n
        F += [np.stack([a, b, d], 1), np.stack([a, d, c], 1)]
    i = np.arange(n)
    bot, top = len(V) - 2, len(V) - 1
    F.append(np.stack([np.full(n, bot), (i + 1) % n, i], 1))
    F.append(np.stack([np.full(n, top), rings * n + i, rings * n + (i + 1) % n], 1))
    return V, np.concatenate(F)


def torus(R: float = 30.0, r: float = 8.0, nu: int = 128, nv: int = 64) -> tuple[np.ndarray, np.ndarray]:
    """A torus around Z, outward normals; rings of vertices lie exactly at z = 0 and y = 0."""
    u = 2 * np.pi * np.arange(nu) / nu
    v = 2 * np.pi * np.arange(nv) / nv
    U, W = np.meshgrid(u, v, indexing="ij")
    V = np.stack([(R + r * np.cos(W)) * np.cos(U), (R + r * np.cos(W)) * np.sin(U), r * np.sin(W)], -1).reshape(-1, 3)
    i, j = np.meshgrid(np.arange(nu), np.arange(nv), indexing="ij")
    i, j = i.ravel(), j.ravel()
    a = i * nv + j
    b = ((i + 1) % nu) * nv + j
    c = ((i + 1) % nu) * nv + (j + 1) % nv
    d = i * nv + (j + 1) % nv
    return V, np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])


def plate_with_hole() -> tuple[np.ndarray, np.ndarray]:
    """A 10 x 6 x 2 mm plate with a 2 x 2 mm square hole through it at x 4..6, y 2..4."""
    m = np.ones((10, 6, 2), bool)
    m[4:6, 2:4, :] = False
    return voxels(m)


def random_pose(seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    return R, rng.uniform(-300, 300, 3)


def frame_of(R: np.ndarray, t: np.ndarray) -> PartFrame:
    """A part frame whose matrix is ``x_part = R x_scan + t``."""
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = t
    return PartFrame(matrix=M, axes=("Z", "X", "Y"), built_from=[], sigma={})


# --------------------------------------------------------------------------- checks
def check_clean(s: sec.Section) -> None:
    """No duplicate points in a line, no zero-length segments, every point on the plane,
    per-segment arrays of the right length."""
    for ln in s.lines:
        k = len(ln.points)
        assert ln.points.shape == (k, 2) and ln.points3d.shape == (k, 3)
        assert k >= (3 if ln.closed else 2)
        n_seg = k if ln.closed else k - 1
        assert len(ln.filled) == n_seg and len(ln.faces) == n_seg
        P = np.vstack([ln.points3d, ln.points3d[:1]]) if ln.closed else ln.points3d
        seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
        assert seg.min() > 0.0, "a zero-length segment"
        assert len(np.unique(ln.points3d, axis=0)) == k, "a point repeated in a line"
        assert np.abs(s.plane.distance(ln.points3d)).max() < 1e-6
        assert np.allclose(s.plane.to_2d(ln.points3d), ln.points, atol=1e-9)


def corner_mask(P: np.ndarray, tol: float = 1e-9) -> np.ndarray:
    """Which points of a closed 2D polygon are corners (where it turns; collinear points are not)."""
    a, b = np.roll(P, 1, axis=0), np.roll(P, -1, axis=0)
    cr = (P[:, 0] - a[:, 0]) * (b[:, 1] - P[:, 1]) - (P[:, 1] - a[:, 1]) * (b[:, 0] - P[:, 0])
    return np.abs(cr) > tol


# --------------------------------------------------------------------------- planes
def test_plane_axes_without_a_frame():
    """The drawing is the cut seen from the side the normal points to; "up" is the scan's Z, or
    its Y when the plane lies within 45° of horizontal; 2D coordinates are measured from where
    the scan's origin falls on the plane."""
    p = sec.plane_from_point_normal([5, 6, 7], [0, 0, 1])
    assert np.allclose(p.u, [1, 0, 0]) and np.allclose(p.v, [0, 1, 0])
    assert np.allclose(p.to_2d([[3, 4, 7]]), [[3, 4]])
    p = sec.plane_from_point_normal([5, 6, 7], [1, 0, 0])
    assert np.allclose(p.u, [0, 1, 0]) and np.allclose(p.v, [0, 0, 1])
    p = sec.plane_from_point_normal([5, 6, 7], [0, -1, 0])  # the front view
    assert np.allclose(p.u, [1, 0, 0]) and np.allclose(p.v, [0, 0, 1])
    p = sec.plane_from_point_normal([5, 6, 7], [0, 2, 0])  # the back view: not mirrored, X runs left
    assert np.allclose(p.u, [-1, 0, 0]) and np.allclose(p.v, [0, 0, 1])
    rng = np.random.default_rng(1)
    for n in rng.normal(size=(50, 3)):
        p = sec.plane_from_point_normal(rng.normal(size=3), n)
        assert np.allclose(np.cross(p.u, p.v), p.normal, atol=1e-12)
        assert abs(p.u @ p.v) < 1e-12 and abs(np.linalg.norm(p.u) - 1) < 1e-12
        X = rng.normal(size=(5, 3)) * 10
        assert np.allclose(p.to_3d(p.to_2d(X)) + np.outer(p.distance(X), p.normal), X)
    # the same plane always gives the same drawing
    a = sec.plane_from_point_normal([1, 2, 3], [0.3, -0.2, 0.9])
    b = sec.plane_from_point_normal([1, 2, 3], [0.3, -0.2, 0.9])
    assert a.to_dict() == b.to_dict()
    json.dumps(a.to_dict())


def test_plane_from_fit_points_and_frame():
    fit = PlaneFit(point=np.array([1.0, 2.0, 3.0]), normal=np.array([0.0, 0.0, 1.0]), rms_mm=0.01, flatness_mm=0.05,
                   n=100, normal_sigma_deg=0.01, offset_sigma_mm=0.001)
    for f in (fit, fit.to_dict(), {"id": "f1", "name": "Top", "fit": fit.to_dict()}):
        p = sec.plane_from_fit(f, offset_mm=2.5)
        assert np.allclose(p.normal, [0, 0, 1]) and abs(p.distance([[0, 0, 5.5]])[0]) < 1e-12
        assert "2.5" in p.description
    assert "Top" in sec.plane_from_fit({"id": "f1", "name": "Top", "fit": fit.to_dict()}).description
    with pytest.raises(ValueError):
        sec.plane_from_fit({"kind": "cylinder", "point": [0, 0, 0], "axis": [0, 0, 1], "radius_mm": 2})
    # three points: the normal by the right-hand rule
    p = sec.plane_from_points([0, 0, 1], [1, 0, 1], [0, 1, 1])
    assert np.allclose(p.normal, [0, 0, 1]) and abs(p.distance([[4, 4, 1]])[0]) < 1e-12
    with pytest.raises(ValueError):
        sec.plane_from_points([0, 0, 0], [1, 1, 1], [2, 2, 2])
    with pytest.raises(ValueError):
        sec.plane_from_point_normal([0, 0, 0], [0, 0, 0])
    with pytest.raises(ValueError):
        sec.plane_from_point_normal([0, 0, np.nan], [0, 0, 1])
    # a plane of the part's frame
    R, t = random_pose(3)
    fr = frame_of(R, t)
    p = sec.plane_from_frame(fr, "Z", 4.0)
    assert np.allclose(p.normal, R[2]) and "Z = 4" in p.description
    X = fr.points_from_part([[1.5, -2.0, 4.0]])
    assert abs(p.distance(X)[0]) < 1e-9 and np.allclose(p.to_2d(X), [[1.5, -2.0]])
    with pytest.raises(ValueError):
        sec.plane_from_frame(fr, "W", 1.0)


@pytest.mark.parametrize("axis, cols, signs", [("Z", (0, 1), (1, 1)), ("X", (1, 2), (1, 1)), ("Y", (0, 2), (-1, 1)),
                                               ("-Y", (0, 2), (1, 1)), ("-Z", (0, 1), (-1, 1))])
def test_frame_axes_are_the_parts(axis, cols, signs):
    """With a part frame the drawing's axes are the part's own: the plane Z = c is drawn in the
    part's (X, Y), X = c in (Y, Z), Y = c seen from -Y (the front view) in (X, Z)."""
    Vp, F = plate_with_hole()
    R, t = random_pose(11)
    fr = frame_of(R, t)
    V = fr.points_from_part(Vp)
    value = {"Z": 1.37, "-Z": 1.37, "X": 7.3, "Y": 2.6, "-Y": 2.6}[axis]
    plane = sec.plane_from_frame(fr, axis, value)
    s = sec.section(V, F, plane)
    assert s.lines
    assert np.allclose(fr.points_to_part(s.lines[0].points3d)[:, "XYZ".index(axis[-1])], value)
    for ln in s.lines:
        Xp = fr.points_to_part(ln.points3d)
        assert np.allclose(ln.points[:, 0], signs[0] * Xp[:, cols[0]], atol=1e-9)
        assert np.allclose(ln.points[:, 1], signs[1] * Xp[:, cols[1]], atol=1e-9)
    check_clean(s)


# --------------------------------------------------------------------------- known shapes
@pytest.mark.parametrize("normal", [[0, 0, 1], [0, 0, -1]])
def test_box_cut_square(normal):
    V, F = voxels(np.ones((8, 6, 4), bool), step=5.0)  # 40 x 30 x 20 mm
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 7.3], normal))
    assert len(s.lines) == 1
    ln = s.lines[0]
    assert ln.closed
    assert abs(ln.length - 140.0) < 1e-9 and abs(ln.area - 1200.0) < 1e-9  # an outline: positive
    c = np.abs(ln.points[corner_mask(ln.points)])
    assert len(c) == 4 and np.allclose(np.sort(c, axis=0)[[0, -1]], [[0, 0], [40, 30]])
    u0, v0, u1, v1 = s.bbox
    assert abs((u1 - u0) - 40) < 1e-9 and abs((v1 - v0) - 30) < 1e-9
    check_clean(s)


def test_box_cut_through_a_corner():
    V, F = voxels(np.ones((8, 6, 4), bool), step=5.0)
    s = sec.section(V, F, sec.plane_from_point_normal([2, 2, 2], [1, 1, 1]))  # x + y + z = 6
    assert len(s.lines) == 1 and s.lines[0].closed
    ln = s.lines[0]
    c3 = ln.points3d[corner_mask(ln.points)]
    assert len(c3) == 3
    want = np.array([[6, 0, 0], [0, 6, 0], [0, 0, 6]], float)
    assert np.allclose(c3[np.lexsort(c3.T[::-1])], want[np.lexsort(want.T[::-1])], atol=1e-9)
    assert abs(ln.area - math.sqrt(3) / 2 * 36) < 1e-9
    assert abs(ln.length - 3 * 6 * math.sqrt(2)) < 1e-9
    check_clean(s)


def test_cylinder_at_an_angle_is_an_ellipse():
    r, n = 10.0, 512
    V, F = cylinder(r, 100.0, n)
    th = math.radians(30)
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 50], [math.sin(th), 0, math.cos(th)]))
    assert len(s.lines) == 1 and s.lines[0].closed
    P = s.lines[0].points
    c = s.plane.to_2d([[0, 0, 50]])[0]  # the ellipse's centre, on the axis
    d = np.linalg.norm(P - c, axis=1)
    sag = r * (1 - math.cos(math.pi / n))  # how far a chord lies inside the round
    assert abs(d.max() - r / math.cos(th)) < 2 * sag / math.cos(th) + 1e-6
    assert abs(d.min() - r) < 2 * sag + 1e-6
    # the plane is within 45° of horizontal, so "up" (v) is Y, and the long axis (along the tilt) is u
    far = P[np.argmax(d)] - c
    assert abs(far[0]) > 0.999 * np.linalg.norm(far)
    assert abs(s.lines[0].area - math.pi * r * r / math.cos(th)) < 1e-4 * math.pi * r * r / math.cos(th)
    check_clean(s)


@pytest.mark.parametrize("z", [-15.0, 0.0, 7.3, 19.0])
def test_sphere_gives_circles(z):
    R = 20.0
    V, F = icosphere(R, 5)
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, z], [0, 0, 1]))
    assert len(s.lines) == 1 and s.lines[0].closed
    rho = math.sqrt(R * R - z * z)
    d = np.linalg.norm(s.lines[0].points, axis=1)  # the circle's centre is the 2D origin
    assert d.max() <= rho + 1e-9  # the facets lie inside the sphere
    assert d.min() >= rho - 0.006 * R / rho
    assert s.lines[0].area > 0.99 * math.pi * rho * rho
    check_clean(s)


def test_torus_gives_two_loops():
    V, F = torus(30.0, 8.0)
    # across the middle (a ring of vertices lies in the plane): an outline and a hole
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 0], [0, 0, 1]))
    assert len(s.lines) == 2 and all(ln.closed for ln in s.lines)
    outer, inner = s.lines  # longest first
    assert outer.area > 0 > inner.area
    assert abs(outer.area / (math.pi * 38**2) - 1) < 2e-3 and abs(-inner.area / (math.pi * 22**2) - 1) < 2e-3
    check_clean(s)
    # through the axis: two round outlines, both positive
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 0], [0, 1, 0]))
    assert len(s.lines) == 2 and all(ln.closed and ln.area > 0 for ln in s.lines)
    for ln in s.lines:
        c = ln.points.mean(axis=0)
        assert abs(abs(c[0]) - 30.0) < 1e-6 and abs(c[1]) < 1e-6
        assert abs(ln.area / (math.pi * 64) - 1) < 3e-3  # a 64-gon in a circle: 1.6e-3 short
    check_clean(s)


def test_open_sheet_gives_an_open_line():
    V, F = grid(41, 21, 0.5, z=lambda x, y: 0.1 * x)
    s = sec.section(V, F, sec.plane_from_point_normal([7.3, 0, 0], [1, 0, 0]))
    assert len(s.lines) == 1
    ln = s.lines[0]
    assert not ln.closed and ln.area is None
    assert abs(ln.length - 10.0) < 1e-9
    assert np.allclose(sorted(ln.points3d[[0, -1], 1]), [0.0, 10.0])
    check_clean(s)


def test_sheet_with_a_hole_gives_two_open_lines():
    V, F = punch(*grid(61, 61, 0.5), centers=[(15, 15, 0)], radii=[5.0])
    s = sec.section(V, F, sec.plane_from_point_normal([0, 15.2, 0], [0, 1, 0]))
    assert len(s.lines) == 2 and not any(ln.closed for ln in s.lines)
    for ln in s.lines:
        ends = np.sort(ln.points3d[[0, -1], 0])
        assert np.isclose(ends[0], 0.0) or np.isclose(ends[1], 30.0)  # from the sheet's edge to the hole's
        assert 9.0 < ln.length < 11.0
    check_clean(s)


def test_solid_plate_with_a_hole():
    V, F = plate_with_hole()
    # across the hole, through vertices and along edges (y = 3 is a grid plane): two rectangles
    s = sec.section(V, F, sec.plane_from_point_normal([0, 3, 0], [0, -1, 0]))
    assert len(s.lines) == 2 and all(ln.closed for ln in s.lines)
    assert np.allclose([ln.area for ln in s.lines], [8.0, 8.0]) and np.allclose([ln.length for ln in s.lines], 12.0)
    check_clean(s)
    # flat through the plate, on a grid plane and off it: the outline and the hole, with opposite signs
    for z in (1.0, 1.37):
        s = sec.section(V, F, sec.plane_from_point_normal([0, 0, z], [0, 0, 1]))
        assert len(s.lines) == 2
        assert np.allclose(sorted(ln.area for ln in s.lines), [-4.0, 60.0])
        assert np.allclose([ln.length for ln in s.lines], [32.0, 8.0])
        check_clean(s)


# --------------------------------------------------------------------------- awkward cases
@pytest.mark.parametrize("point, normal, length", [
    ([7, 0, 0], [1, 0, 0], 20.0),  # along grid edges
    ([0, 0, 0], [1, -1, 0], 20 * math.sqrt(2)),  # along the diagonals
    ([10, 0, 0], [1, 1, 0], 10 * math.sqrt(2)),  # through vertices and the diagonals' midpoints
    ([10.5, 0, 0], [1, 1, 0], 10.5 * math.sqrt(2)),  # between the vertices
])
def test_plane_through_grid_vertices_and_edges(point, normal, length):
    V, F = grid(21, 21, 1.0)
    s = sec.section(V, F, sec.plane_from_point_normal(point, normal))
    assert len(s.lines) == 1 and not s.lines[0].closed
    assert abs(s.lines[0].length - length) < 1e-9
    check_clean(s)
    # a hair off the plane gives the same line
    for eps in (1e-7, -1e-7):
        s2 = sec.section(V, F, sec.plane_from_point_normal(np.asarray(point) + eps * np.asarray(normal), normal))
        assert len(s2.lines) == 1 and abs(s2.lines[0].length - length) < 1e-5


def test_closed_grid_cut_along_its_edges():
    V, F = voxels(np.ones((10, 6, 4), bool))
    for point, normal, area, length in (([5, 0, 0], [1, 0, 0], 24.0, 20.0), ([0, 0, 2], [0, 0, -1], 60.0, 32.0),
                                        ([3, 3, 0], [1, 1, 0], 4 * 6 * math.sqrt(2), 8 + 12 * math.sqrt(2))):
        s = sec.section(V, F, sec.plane_from_point_normal(point, normal))
        assert len(s.lines) == 1 and s.lines[0].closed
        assert abs(s.lines[0].area - area) < 1e-9 and abs(s.lines[0].length - length) < 1e-9
        check_clean(s)


@pytest.mark.parametrize("normal", [[0, 0, 1], [0, 0, -1]])
def test_faces_lying_in_the_plane(normal):
    """The box's top face lies in the plane: its outline is the cut (the diagonal between its two
    triangles is not), whichever way the normal points."""
    V, F = voxels(np.ones((10, 6, 4), bool))
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 4], normal))
    assert len(s.lines) == 1 and s.lines[0].closed
    assert abs(s.lines[0].area - 60.0) < 1e-9 and abs(s.lines[0].length - 32.0) < 1e-9
    check_clean(s)
    # one lone triangle in the plane: its three sides
    s = sec.section(np.array([[0, 0, 0], [3, 0, 0], [0, 4, 0.0]]), np.array([[0, 1, 2]]),
                    sec.plane_from_point_normal([0, 0, 0], [0, 0, 1]))
    assert len(s.lines) == 1 and s.lines[0].closed and len(s.lines[0].points) == 3
    assert abs(s.lines[0].length - 12.0) < 1e-12 and abs(s.lines[0].area - 6.0) < 1e-12


def test_a_vertex_touching_the_plane_is_no_line():
    V, F = icosphere(10.0, 2)
    s = sec.section(V, F, sec.plane_from_point_normal(V[0], V[0]))  # touching the sphere at one vertex
    assert s.lines == []
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 50], [0, 0, 1]))
    assert s.lines == [] and s.bbox is None and s.length == 0.0
    s = sec.section(V, np.zeros((0, 3), np.int64), sec.plane_from_point_normal([0, 0, 0], [0, 0, 1]))
    assert s.lines == []


def test_saddle_point_in_the_plane():
    """z = x^2 - y^2 cut at z = 0: the two diagonals cross at a vertex lying in the plane, where four
    arms meet. They are paired so that no line crosses itself or passes a point twice."""
    V, F = grid(21, 21, 1.0)
    V[:, :2] -= 10.0
    V[:, 2] = V[:, 0] ** 2 - V[:, 1] ** 2
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 0], [0, 0, 1]))
    assert abs(s.length - 40 * math.sqrt(2)) < 1e-9
    assert len(s.lines) == 2 and not any(ln.closed for ln in s.lines)
    for ln in s.lines:  # each line turns at the saddle (one corner, there), it does not cross over
        P = ln.points
        a, b = P[1:-1] - P[:-2], P[2:] - P[1:-1]
        turn = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) > 1e-9
        assert turn.sum() == 1 and np.allclose(P[1:-1][turn], [[0, 0]])
        assert abs(ln.length - 20 * math.sqrt(2)) < 1e-9
    check_clean(s)


def test_non_manifold_fin_and_duplicate_faces():
    """Three faces on one edge (a fin): the three half-lines meet at one point, nothing is lost or
    counted twice; a face given twice is cut once."""
    V = np.array([[0, 0, -1], [0, 0, 1], [2, 0, 0.1], [-1, 1.7, 0.2], [-1, -1.7, -0.2]], float)
    F = np.array([[0, 1, 2], [1, 0, 3], [0, 1, 4], [0, 1, 2]])
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 0.0], [0, 0, 1]))
    # brute force: each distinct face's piece
    want = 0.0
    for f in np.unique(np.sort(F, axis=1), axis=0):
        a, b, c = V[f]
        P = [p0 + (p1 - p0) * (-p0[2]) / (p1[2] - p0[2]) for p0, p1 in ((a, b), (b, c), (c, a)) if p0[2] * p1[2] < 0]
        want += np.linalg.norm(P[0] - P[1])
    assert abs(s.length - want) < 1e-12
    assert sum(len(ln.faces) for ln in s.lines) == 3
    check_clean(s)


def test_random_soup_conserves_length():
    rng = np.random.default_rng(7)
    for trial in range(20):
        V = rng.uniform(-10, 10, (200, 3))
        F = rng.integers(0, 200, (800, 3))
        F = np.vstack([F, F[:50, [1, 2, 0]]])  # some faces twice
        plane = sec.plane_from_point_normal(rng.uniform(-2, 2, 3), rng.normal(size=3))
        s = sec.section(V, F, plane)
        d = plane.distance(V)
        want = 0.0
        for f in np.unique(np.sort(F, axis=1), axis=0):
            if len(set(f.tolist())) < 3:
                continue
            P = [V[i] + (V[j] - V[i]) * d[i] / (d[i] - d[j]) for i, j in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0]))
                 if d[i] * d[j] < 0]
            if len(P) == 2:
                want += np.linalg.norm(P[0] - P[1])
        assert abs(s.length - want) < 1e-9 * max(want, 1.0), trial
        check_clean(s)
    # with many vertices exactly in the plane: no crash, nothing degenerate
    for trial in range(20):
        V = rng.uniform(-10, 10, (100, 3))
        V[rng.random(100) < 0.4, 2] = 0.0
        F = rng.integers(0, 100, (400, 3))
        s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 0], [0, 0, 1]))
        check_clean(s)


def test_the_same_cut_in_another_pose():
    for mesh, point, normal in ((plate_with_hole(), [0, 0, 1.0], [0, 0, 1]), (plate_with_hole(), [0, 3, 0], [0, 1, 0]),
                                (torus(), [0, 0, 0], [0, 0, 1]), (torus(), [3, 1, 2], [0.2, 0.5, 0.8])):
        V, F = mesh
        s0 = sec.section(V, F, sec.plane_from_point_normal(point, normal))
        for seed in range(3):
            R, t = random_pose(seed)
            s1 = sec.section(V @ R.T + t, F, sec.plane_from_point_normal(R @ point + t, R @ normal))
            assert len(s1.lines) == len(s0.lines)
            assert np.allclose([ln.length for ln in s1.lines], [ln.length for ln in s0.lines], atol=1e-6)
            assert np.allclose([ln.area for ln in s1.lines], [ln.area for ln in s0.lines], atol=1e-6)
            assert [ln.closed for ln in s1.lines] == [ln.closed for ln in s0.lines]
            check_clean(s1)


# --------------------------------------------------------------------------- sigma and filled faces
def test_sigma_is_carried_and_interpolated():
    V, F = icosphere(20.0, 4)
    sigma = 0.05 + 0.001 * (V @ [1.0, 2.0, 3.0])
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 3.3], [0.1, 0.2, 1.0]), sigma=sigma)
    assert s.lines
    for ln in s.lines:
        assert ln.sigma.shape == (len(ln.points),)
        assert np.allclose(ln.sigma, 0.05 + 0.001 * (ln.points3d @ [1.0, 2.0, 3.0]), atol=1e-12)
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 3.3], [0, 0, 1]))
    assert all(ln.sigma is None for ln in s.lines)
    with pytest.raises(ValueError):
        sec.section(V, F, sec.plane_from_point_normal([0, 0, 0], [0, 0, 1]), sigma=np.ones(3))
    with pytest.raises(ValueError):
        sec.section(V, F, sec.plane_from_point_normal([0, 0, 0], [0, 0, 1]), filled=np.ones(3, bool))


def test_cut_through_filled_faces_is_marked():
    V, F = plate_with_hole()
    filled = V[F].mean(axis=1)[:, 0] > 5.0
    for point, normal in (([0, 0, 1.37], [0, 0, 1]), ([0, 0, 1.0], [0, 0, 1]), ([0, 3, 0], [0, -1, 0])):
        s = sec.section(V, F, sec.plane_from_point_normal(point, normal), filled=filled)
        kinds = set()
        for ln in s.lines:
            P = np.vstack([ln.points3d, ln.points3d[:1]]) if ln.closed else ln.points3d
            mid = (P[1:] + P[:-1]) / 2
            assert np.array_equal(ln.filled, mid[:, 0] > 5.0)
            assert np.array_equal(ln.filled, filled[ln.faces])
            st = ln.stretches()
            assert sum(len(p) if closed else len(p) - 1 for p, _, closed in st) == len(ln.filled)
            if len(st) == 1:
                assert st[0][2] == ln.closed
            else:
                assert not any(closed for _, _, closed in st)
                pairs = list(zip(st, st[1:] + st[:1])) if ln.closed else list(zip(st, st[1:]))
                for (p, f, _), (q, g, _) in pairs:
                    assert np.array_equal(p[-1], q[0]) and f != g  # they join up, and alternate
            kinds.update(f for _, f, _ in st)
        assert kinds == {True, False}
    # nothing filled: one closed stretch per loop
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 1.37], [0, 0, 1]), filled=np.zeros(len(F), bool))
    assert all(len(ln.stretches()) == 1 and ln.stretches()[0][2] for ln in s.lines)


def test_to_dict_is_plain_json():
    V, F = plate_with_hole()
    s = sec.section(V, F, sec.plane_from_point_normal([0, 0, 1.37], [0, 0, 1]), sigma=np.full(len(V), 0.02),
                    filled=V[F].mean(axis=1)[:, 0] > 5.0)
    d = json.loads(json.dumps(s.to_dict()))
    assert d["plane"]["description"] == s.plane.description
    assert len(d["lines"]) == 2 and d["lines"][0]["closed"] is True
    assert abs(d["lines"][0]["area_mm2"] - 60.0) < 1e-6 and len(d["lines"][0]["points"]) == len(s.lines[0].points)


def test_a_million_triangles_in_well_under_a_second():
    """A noisy sheet of about a million triangles, cut across: the cut itself is timed."""
    rng = np.random.default_rng(5)
    V, F = grid(709, 709, 0.5, z=lambda x, y: np.sin(x / 7.0) * 5.0)
    V[:, 2] += rng.normal(0, 0.05, len(V))
    assert len(F) > 1_000_000
    plane = sec.plane_from_point_normal([100.1, 100.3, 0], [1, 0.3, 0.2])
    best = math.inf
    for _ in range(2):
        t0 = time.perf_counter()
        s = sec.section(V, F, plane, sigma=np.full(len(V), 0.03), filled=np.zeros(len(F), bool))
        best = min(best, time.perf_counter() - t0)
    assert len(s.lines) == 1 and not s.lines[0].closed
    assert best < 1.0, f"{best:.2f} s"
