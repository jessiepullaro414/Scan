"""ohs.measure.region: one click grows a region and fits a plane, a cylinder or a hole, on meshes with
known truth.

The meshes are built here (and from ``mesh_shapes``): a plate whose holes have exact circular edges
(a Delaunay triangulation with the circle's points), a boss (r 7 mm) and a bore (r 6 mm) on it, a
box, a flat sheet that bends away with a 30 mm radius, a gently curved panel, a plate with a
Ø 25 mm hole. Vertices get Gaussian noise of a known per-vertex sigma (0.02-0.05 mm), passed as the
mesh's sigma. Every random generator is seeded.

* Plane: on a box face it stays on that face; on the plate it stops at the boss and the bore; on
  the boss top it finds z = 8. It does not run away where a flat face bends away (the normal test
  stops it with a loose tolerance; without the normal test it goes further) nor over a curved
  panel (bounded, and warned about).
* Cylinder: the boss and the bore, radius and axis within 4σ and 0.05 mm / 0.5°, the region on the
  wall only.
* Hole: Ø 25 within 0.05 mm on an exact edge; on a punched grid (jagged edge) the bias is
  measured; a click near no hole, or on a bore's wall, raises and suggests Cylinder.
* Filled faces (and vertices without sigma) are left out and warned about; a filled hole is
  found again by its edge.
* Small regions are warned about; bad input raises ValueError; the result is plain JSON.
* Speed: clicks on 1M-triangle meshes (slow test).
"""
from __future__ import annotations

import json
import math
import time

import numpy as np
import pytest

from mesh_shapes import box, grid, punch
from ohs.measure import fit_at
from ohs.mesh import fill_holes
from ohs.mesh.confidence import SIGMA_NONE_MM

Z = np.array([0.0, 0.0, 1.0])
H = 0.5  # mm, the plates' mesh spacing
BOSS = (-20.0, 0.0, 7.0, 8.0)  # x, y, radius, height
BORE = (20.0, 0.0, 6.0, 10.0)  # x, y, radius, depth (open at the bottom)


# --------------------------------------------------------------------------- meshes
def plate(width: float, depth: float, h: float, circles=()) -> tuple[np.ndarray, np.ndarray, list[np.ndarray]]:
    """A flat plate at z = 0 (normals +z) over ``width x depth`` mm centred at the origin, with round
    holes ``(cx, cy, r)`` whose edges are exact circles. Returns ``(V, F, ring vertex ids per hole)``
    (each ring counter-clockwise seen from +z)."""
    from scipy.spatial import Delaunay

    xs = np.arange(-width / 2, width / 2 + 1e-9, h)
    ys = np.arange(-depth / 2, depth / 2 + 1e-9, h)
    X, Y = np.meshgrid(xs, ys)
    pts = np.c_[X.ravel(), Y.ravel()]
    for cx, cy, r in circles:
        pts = pts[np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) > r + 0.7 * h]
    rings = []
    for cx, cy, r in circles:
        n = max(12, int(round(2 * math.pi * r / h)))
        a = 2 * math.pi * np.arange(n) / n
        rings.append(np.arange(len(pts), len(pts) + n))
        pts = np.vstack([pts, np.c_[cx + r * np.cos(a), cy + r * np.sin(a)]])
    tri = Delaunay(pts).simplices
    cen = pts[tri].mean(axis=1)
    inside = np.zeros(len(tri), dtype=bool)
    for cx, cy, r in circles:
        inside |= np.hypot(cen[:, 0] - cx, cen[:, 1] - cy) < r
    tri = tri[~inside]
    e1, e2 = pts[tri[:, 1]] - pts[tri[:, 0]], pts[tri[:, 2]] - pts[tri[:, 0]]
    cz = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    tri = np.where((cz < 0)[:, None], tri[:, [0, 2, 1]], tri)[np.abs(cz) > 1e-9]
    return np.c_[pts, np.zeros(len(pts))], tri.astype(np.int64), rings


def wall(V: np.ndarray, ring: np.ndarray, center, r: float, dz: float, h: float):
    """A cylinder wall of radius ``r`` from ``ring`` (vertex ids on the circle, counter-clockwise from
    +z) over ``dz`` mm (up if positive, down if negative): rings every ``h`` mm. Up, the normals
    point outward (a boss); down, inward (a bore). Returns ``(V, new faces, ids of the last ring)``."""
    n = len(ring)
    a = 2 * math.pi * np.arange(n) / n
    k = max(1, int(round(abs(dz) / h)))
    ids = [ring]
    new = []
    for j in range(1, k + 1):
        z = V[ring[0], 2] + dz * j / k
        ids.append(np.arange(len(V) + len(new) * n, len(V) + (len(new) + 1) * n))
        new.append(np.c_[center[0] + r * np.cos(a), center[1] + r * np.sin(a), np.full(n, z)])
    V = np.vstack([V] + new)
    faces = []
    for j in range(k):
        A, B = ids[j], np.roll(ids[j], -1)
        C, D = np.roll(ids[j + 1], -1), ids[j + 1]
        faces += [np.c_[A, B, C], np.c_[A, C, D]]
    return V, np.vstack(faces), ids[-1]


def disc_top(V: np.ndarray, ring: np.ndarray, center, r: float, h: float):
    """A flat disc (normals +z) closing ``ring`` (a circle at one height). Returns ``(V, new faces)``."""
    from scipy.spatial import Delaunay

    xs = np.arange(-r, r + 1e-9, h)
    X, Y = np.meshgrid(xs, xs)
    inner = np.c_[X.ravel(), Y.ravel()]
    inner = inner[np.hypot(inner[:, 0], inner[:, 1]) < r - 0.7 * h] + np.asarray(center[:2])
    pts = np.vstack([V[ring, :2], inner])
    tri = Delaunay(pts).simplices
    e1, e2 = pts[tri[:, 1]] - pts[tri[:, 0]], pts[tri[:, 2]] - pts[tri[:, 0]]
    cz = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    tri = np.where((cz < 0)[:, None], tri[:, [0, 2, 1]], tri)[np.abs(cz) > 1e-9]
    ids = np.concatenate([ring, np.arange(len(V), len(V) + len(inner))])
    V = np.vstack([V, np.c_[inner, np.full(len(inner), V[ring[0], 2])]])
    return V, ids[tri]


def bracket():
    """An 80 x 50 mm plate with the boss (r 7, 8 high, flat top) and the bore (r 6, 10 deep, open at
    the bottom). Returns ``(V, F, labels)``: each face's part, "plate", "boss", "top" or "bore"."""
    bx, by, br, bh = BOSS
    qx, qy, qr, qd = BORE
    V, F, (ring_boss, ring_bore) = plate(80.0, 50.0, H, [(bx, by, br), (qx, qy, qr)])
    labels = ["plate"] * len(F)
    V, Fw, top = wall(V, ring_boss, (bx, by), br, bh, H)
    F = np.vstack([F, Fw])
    labels += ["boss"] * len(Fw)
    V, Ft = disc_top(V, top, (bx, by), br, H)
    F = np.vstack([F, Ft])
    labels += ["top"] * len(Ft)
    V, Fb, _ = wall(V, ring_bore, (qx, qy), qr, -qd, H)
    F = np.vstack([F, Fb])
    labels += ["bore"] * len(Fb)
    return V, F, np.array(labels)


def noisy(V: np.ndarray, seed: int, lo: float = 0.02, hi: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """``V`` with Gaussian noise of a per-vertex sigma drawn from ``[lo, hi]`` mm, and those sigmas."""
    rng = np.random.default_rng(seed)
    S = rng.uniform(lo, hi, len(V))
    return V + rng.normal(size=V.shape) * S[:, None], S


def nearest_face(V: np.ndarray, F: np.ndarray, p) -> tuple[int, np.ndarray]:
    """The face whose centroid is nearest ``p``, and that centroid (a click)."""
    c = V[F].mean(axis=1)
    f = int(np.argmin(np.linalg.norm(c - np.asarray(p, float), axis=1)))
    return f, c[f]


def angle_deg(a, b) -> float:
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(a, b))), abs(float(np.dot(a, b)))))


@pytest.fixture(scope="module")
def bracket_mesh():
    V, F, labels = bracket()
    Vn, S = noisy(V, 1)
    return Vn, F, S, labels


# --------------------------------------------------------------------------- planes
def test_plane_on_a_box_face_stays_on_that_face():
    V, F = box(20.0, 32)
    Vn, S = noisy(V, 2)
    on_top = np.all(V[F][:, :, 2] > 10.0 - 1e-9, axis=1)  # the faces of the +z side
    face, point = nearest_face(Vn, F, (1.0, -2.0, 10.0))
    r = fit_at(Vn, F, face, point, "plane", sigma=S)
    assert on_top[r.faces].all()  # nothing round the edges
    assert len(r.faces) >= 0.97 * on_top.sum()  # and nearly all of the side
    assert angle_deg(r.fit.normal, Z) < max(4 * r.fit.normal_sigma_deg, 1e-3) and r.fit.normal_sigma_deg < 0.05
    assert abs(r.fit.point[2] - 10.0) < 4 * r.fit.offset_sigma_mm
    assert r.fit.normal @ Z > 0 and not r.warnings


def test_plane_on_the_plate_stops_at_the_boss_and_the_bore(bracket_mesh):
    V, F, S, labels = bracket_mesh
    face, point = nearest_face(V, F, (0.0, 15.0, 0.0))
    r = fit_at(V, F, face, point, "plane", sigma=S)
    assert set(labels[r.faces]) == {"plate"}
    assert len(r.faces) >= 0.97 * (labels == "plate").sum()
    assert angle_deg(r.fit.normal, Z) < 4 * r.fit.normal_sigma_deg
    assert abs(r.fit.point[2]) < 4 * r.fit.offset_sigma_mm
    # the default tolerance: 3 times the seed's sigma or scatter (0.02-0.05 mm here)
    assert 0.06 <= r.tolerance_mm <= 0.2 and 10.0 <= r.angle_tol_deg <= 30.0
    assert r.mean_sigma_mm == pytest.approx(0.035, abs=0.003)


def test_plane_on_the_boss_top(bracket_mesh):
    V, F, S, labels = bracket_mesh
    bx, by, _, bh = BOSS
    face, point = nearest_face(V, F, (bx + 2.0, by - 1.0, bh))
    r = fit_at(V, F, face, point, "plane", sigma=S)
    assert set(labels[r.faces]) == {"top"} and len(r.faces) >= 0.95 * (labels == "top").sum()
    assert abs(r.fit.point[2] - bh) < 4 * r.fit.offset_sigma_mm
    assert angle_deg(r.fit.normal, Z) < 4 * r.fit.normal_sigma_deg


def bend(R: float = 30.0) -> tuple[np.ndarray, np.ndarray]:
    """A sheet, flat for x <= 0, then bending up with radius ``R`` (tangent at x = 0) to x = 20."""
    def z(x, y):
        xx = np.clip(x, 0.0, None)
        return R - np.sqrt(R * R - xx * xx)
    V, F = grid(101, 61, H, z=None)
    V[:, 0] -= 30.0
    V[:, 1] -= 15.0
    V[:, 2] = z(V[:, 0], V[:, 1])
    return V, F


def test_plane_does_not_run_away_where_a_flat_face_bends_away():
    """A smooth surface (0.005 mm noise, the face normals within about 1°), so that where the
    region stops is sharp: with noisier normals the angle's limit blurs by their scatter."""
    R = 30.0
    V, F = bend(R)
    Vn, S = noisy(V, 3, 0.005, 0.005)
    face, point = nearest_face(Vn, F, (-15.0, 0.0, 0.0))
    reach = lambda r: float(V[np.unique(F[r.faces]), 0].max())  # noqa: E731 (how far into the bend)
    # the default tolerance (3 sigma): the distance stops it where the bend has risen by it
    r = fit_at(Vn, F, face, point, "plane", sigma=S)
    assert reach(r) <= math.sqrt(2 * R * r.tolerance_mm) + 2 * H
    # a loose 2 mm tolerance: the normal test stops it where the bend has turned by its angle
    loose = fit_at(Vn, F, face, point, "plane", sigma=S, tolerance_mm=2.0)
    limit = R * math.sin(math.radians(loose.angle_tol_deg + 1.0)) + 2 * H
    assert reach(loose) <= limit
    # without the normal test, the same tolerance lets the plane creep further up the bend
    free = fit_at(Vn, F, face, point, "plane", sigma=S, tolerance_mm=2.0, angle_tol_deg=90.0)
    assert reach(free) > limit + 1.0
    assert angle_deg(loose.fit.normal, Z) < angle_deg(free.fit.normal, Z)


def test_a_too_pessimistic_sigma_does_not_loosen_the_region():
    """A scan's sigma can claim far more noise than the surface shows (a short simulated scan:
    about 3 mm claimed, 0.4 mm seen). The tolerance then comes from the measured scatter, so the
    plane stops in the bend as with the true sigma, instead of creeping up it."""
    R = 30.0
    V, F = bend(R)
    Vn, S = noisy(V, 3, 0.005, 0.005)
    face, point = nearest_face(Vn, F, (-15.0, 0.0, 0.0))
    reach = lambda r: float(V[np.unique(F[r.faces]), 0].max())  # noqa: E731 (how far into the bend)
    true = fit_at(Vn, F, face, point, "plane", sigma=S)
    claimed = fit_at(Vn, F, face, point, "plane", sigma=S * 20.0)  # sigma 20 times too large
    assert claimed.tolerance_mm <= 2.0 * true.tolerance_mm
    assert reach(claimed) <= math.sqrt(2 * R * claimed.tolerance_mm) + 2 * H
    assert reach(claimed) <= reach(true) + 1.0


def test_plane_on_a_gently_curved_panel_stays_bounded_and_warns():
    R = 300.0
    V, F = grid(121, 61, H, z=lambda x, y: (x - 30.0) ** 2 / (2 * R))
    Vn, S = noisy(V, 4, 0.02, 0.02)
    face, point = nearest_face(Vn, F, (30.0, 15.0, 0.0))
    r = fit_at(Vn, F, face, point, "plane", sigma=S)
    xs = V[np.unique(F[r.faces]), 0]
    # the best plane over a strip of width L strays L^2 / (12 R) at its edges: L = sqrt(12 R tol)
    L = math.sqrt(12 * R * r.tolerance_mm)
    assert xs.max() - xs.min() < 1.5 * L  # bounded, far from the panel's 60 mm
    assert any("scatter" in w for w in r.warnings)  # not flat within its sigma: said so


# --------------------------------------------------------------------------- cylinders
@pytest.mark.parametrize("part", ["boss", "bore"])
def test_cylinder_on_the_boss_and_the_bore(bracket_mesh, part):
    V, F, S, labels = bracket_mesh
    x, y, radius, height = BOSS if part == "boss" else BORE
    z = 0.4 * height if part == "boss" else -0.4 * height
    face, point = nearest_face(V, F, (x + radius * math.cos(1.0), y + radius * math.sin(1.0), z))
    r = fit_at(V, F, face, point, "cylinder", sigma=S)
    fit = r.fit
    assert set(labels[r.faces]) == {part}  # the wall only: not the plate, not the boss's top
    assert len(r.faces) >= 0.95 * (labels == part).sum()
    assert abs(fit.radius_mm - radius) < max(4 * fit.radius_sigma_mm, 1e-3) and abs(fit.radius_mm - radius) < 0.05
    assert angle_deg(fit.axis, Z) < 4 * fit.axis_sigma_deg and angle_deg(fit.axis, Z) < 0.5
    off = math.hypot(fit.point[0] - x, fit.point[1] - y)
    assert off < 4 * fit.point_sigma_mm and off < 0.1
    assert fit.rms_mm == pytest.approx(0.032, abs=0.005)  # weighted by 1/sigma^2: sqrt(1 / mean(1/sigma^2))


def test_cylinder_on_a_flat_plate_raises(bracket_mesh):
    V, F, S, _ = bracket_mesh
    face, point = nearest_face(V, F, (0.0, 15.0, 0.0))
    with pytest.raises(ValueError, match="does not fix a cylinder"):
        fit_at(V, F, face, point, "cylinder", sigma=S)


def sheet(xs: np.ndarray, ys: np.ndarray, height, noise: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """A height field ``z = height(x, y)`` over the grid ``xs`` x ``ys`` with Gaussian noise on z, two
    triangles a cell."""
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    Z = height(X, Y) + np.random.default_rng(seed).normal(0.0, noise, X.shape)
    V = np.stack([X, Y, Z], axis=-1).reshape(-1, 3)
    idx = np.arange(X.size).reshape(X.shape)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel(), idx[:-1, 1:].ravel()
    return V, np.concatenate([np.stack([a, b, c], axis=1), np.stack([a, c, d], axis=1)])


def test_cylinder_on_a_noisy_plate_with_a_shallow_bump_raises():
    """A flat, noisy plate (1 mm, slightly bowed, as a short scan's table is) with a shallow bump at
    the click: the seed sees a cylinder of radius 250 mm in the bump, and the region then runs over
    the plate, where the radius goes to metres. That is not a cylinder, and the answer says so
    (it was a cylinder of radius 4.9 m; on a real 10-frame scan, 22.6 m)."""
    r, half = 250.0, 40.0

    def height(X, Y):
        bump = np.sqrt(r * r - np.minimum(X * X, half * half)) - math.sqrt(r * r - half * half)
        return np.where(np.abs(X) < half, bump, 0.0) - 2.5 * (X / 200.0) ** 2

    V, F = sheet(np.arange(-200.0, 201.0, 2.0), np.arange(-150.0, 151.0, 2.0), height, noise=1.0, seed=2)
    face, point = nearest_face(V, F, (0.0, 0.0, 0.0))
    with pytest.raises(ValueError, match=r"not one cylinder: its radius is \d+ mm at the click and \d+ mm over the region"):
        fit_at(V, F, face, point, "cylinder")
    assert fit_at(V, F, face, point, "plane").fit.n > 500  # a plane is what it is


def test_cylinder_on_a_rim_that_curls_ever_tighter_raises():
    """The curled rim of a scan: flat, then bending ever tighter towards the edge (curvature 1/12 mm
    at the edge). The seed at the edge sees a radius of about 25 mm, the region that grows from it
    one of about 50 mm: the radius is not one radius (on a real scan's rim: 26 mm and 52 mm)."""
    w, r0 = 40.0, 12.0

    def height(X, Y):
        u = np.clip(w - X, 0.0, w)  # curvature (1 / r0) (u / w)^3: none at x = w and beyond
        return -(u ** 5) / (20.0 * r0 * w ** 3)

    V, F = sheet(np.arange(0.0, 100.0, 1.0), np.arange(0.0, 300.0, 1.0), height, noise=0.3, seed=1)
    for x in (2.0, 5.0):
        face, point = nearest_face(V, F, (x, 150.0, float(height(np.float64(x), 0.0))))
        with pytest.raises(ValueError, match="not one cylinder"):
            fit_at(V, F, face, point, "cylinder")


# --------------------------------------------------------------------------- holes
def test_hole_diameter_on_an_exact_edge():
    V, F, (ring,) = plate(80.0, 80.0, H, [(5.0, -3.0, 12.5)])
    Vn, S = noisy(V, 5)
    face, point = nearest_face(Vn, F, (5.0 + 16.0, -3.0, 0.0))  # 3.5 mm from the edge
    r = fit_at(Vn, F, face, point, "hole", sigma=S)
    c = r.fit
    assert abs(c.diameter_mm - 25.0) < 4 * c.diameter_sigma_mm and abs(c.diameter_mm - 25.0) < 0.05
    assert math.hypot(c.center[0] - 5.0, c.center[1] + 3.0) < 4 * c.center_sigma_mm
    assert angle_deg(c.normal, Z) < 0.2 and c.n == len(ring)
    assert set(r.edge.tolist()) == set(ring.tolist())
    assert len(r.faces) > 0 and np.all(np.linalg.norm(V[F[r.faces]][:, :, :2].mean(axis=1) - (5.0, -3.0), axis=1) < 12.5 + 6.0)
    assert not r.warnings and r.to_dict()["fit"]["diameter_mm"] == pytest.approx(c.diameter_mm)


def test_hole_bias_on_a_punched_grid():
    """``punch`` drops the faces whose centroid is within the radius: the edge left is a staircase
    of grid vertices (0.18 mm RMS off the circle at 0.5 mm spacing). Its points sit about as much
    in as out, so the diameter reads only a little large: measured +0.03 mm at 0.5 mm spacing,
    +0.01 mm at 0.25 mm. (Fusion's rounding of small holes is another matter: step 5 measures it.)"""
    V, F = grid(161, 161, H)
    V[:, :2] -= 40.0
    V, F = punch(V, F, [(0.0, 0.0, 0.0)], [12.5])
    face, point = nearest_face(V, F, (15.0, 0.0, 0.0))
    r = fit_at(V, F, face, point, "hole")
    bias = r.fit.diameter_mm - 25.0
    assert 0.0 < bias < 0.1
    assert math.hypot(*r.fit.center[:2]) < 0.01 and not r.warnings


def test_hole_click_near_no_hole_on_a_border_or_on_a_bore_wall_raises(bracket_mesh):
    V, F, S, _ = bracket_mesh
    face, point = nearest_face(V, F, (0.0, 8.0, 0.0))  # 17 mm from the plate's border, 19 from the bore's end
    with pytest.raises(ValueError, match="no hole edge within 10 mm"):
        fit_at(V, F, face, point, "hole", sigma=S)
    face, point = nearest_face(V, F, (0.0, 22.0, 0.0))  # 3 mm from the plate's outer border
    with pytest.raises(ValueError, match="outer border"):
        fit_at(V, F, face, point, "hole", sigma=S)
    qx, qy, qr, qd = BORE
    face, point = nearest_face(V, F, (qx + qr, qy, -qd + 2.0))  # on the wall, 2 mm above its open end
    with pytest.raises(ValueError, match="Cylinder"):
        fit_at(V, F, face, point, "hole", sigma=S)


# --------------------------------------------------------------------------- filled faces, small regions
def test_filled_faces_are_left_out_and_warned():
    V, F, (ring,) = plate(60.0, 60.0, H, [(0.0, 0.0, 12.5)])
    Vn, S = noisy(V, 6)
    V2, F2 = fill_holes(Vn, F, 200.0)  # fill the hole: its new faces come after the old ones
    filled = np.arange(len(F2)) >= len(F)
    S2 = np.concatenate([S, np.full(len(V2) - len(Vn), SIGMA_NONE_MM)])
    assert filled.sum() > 0
    face, point = nearest_face(V2, F2, (20.0, 20.0, 0.0))
    r = fit_at(V2, F2, face, point, "plane", sigma=S2, filled=filled)
    assert not filled[r.faces].any() and len(r.faces) >= 0.97 * len(F)
    assert any("filled" in w for w in r.warnings)
    # the filled hole is found again by its edge
    face, point = nearest_face(V2, F2, (14.0, 0.0, 0.0))
    h = fit_at(V2, F2, face, point, "hole", sigma=S2, filled=filled)
    assert abs(h.fit.diameter_mm - 25.0) < 0.05 and set(h.edge.tolist()) == set(ring.tolist())
    assert any("filled" in w for w in h.warnings)
    # vertices without a sigma count as not measured too
    S3 = S.copy()
    S3[np.flatnonzero(np.hypot(V[:, 0] - 20.0, V[:, 1]) < 3.0)] = np.nan
    face, point = nearest_face(Vn, F, (-20.0, -20.0, 0.0))
    r = fit_at(Vn, F, face, point, "plane", sigma=S3)
    assert np.isfinite(S3[F[r.faces]]).all() and any("filled" in w or "not measured" in w for w in r.warnings)
    # a click on a filled face
    with pytest.raises(ValueError, match="not measured"):
        fit_at(V2, F2, int(np.flatnonzero(filled)[0]), V2[F2[np.flatnonzero(filled)[0]]].mean(axis=0), "plane",
               sigma=S2, filled=filled)


def test_a_small_region_is_warned_about():
    V, F = grid(5, 5, 1.0)  # 25 points
    Vn, S = noisy(V, 7, 0.01, 0.01)
    r = fit_at(Vn, F, 10, Vn[F[10]].mean(axis=0), "plane", sigma=S)
    assert r.fit.n == 25 and any(w.startswith("small region: 25 points") for w in r.warnings)


# --------------------------------------------------------------------------- input and output
def test_bad_input_raises_a_plain_value_error(bracket_mesh):
    V, F, S, _ = bracket_mesh
    face, point = nearest_face(V, F, (0.0, 15.0, 0.0))
    for kwargs, message in [
        (dict(kind="sphere"), "kind must be"),
        (dict(face=len(F)), "not in the mesh"),
        (dict(point=[0.0, np.nan, 0.0]), "point"),
        (dict(sigma=S[:-1]), "one sigma per vertex"),
        (dict(filled=np.zeros(3, bool)), "one flag per face"),
        (dict(tolerance_mm=-1.0), "tolerance_mm"),
    ]:
        args = dict(V=V, F=F, face=face, point=point, kind="plane", sigma=S) | kwargs
        with pytest.raises(ValueError, match=message):
            fit_at(**args)


def test_results_are_plain_json(bracket_mesh):
    V, F, S, _ = bracket_mesh
    face, point = nearest_face(V, F, (0.0, 15.0, 0.0))
    d = fit_at(V, F, face, point, "plane", sigma=S).to_dict()
    assert json.loads(json.dumps(d)) == d
    assert set(d) == {"kind", "fit", "faces", "edge", "warnings", "tolerance_mm", "angle_tol_deg", "mean_sigma_mm"}
    assert d["kind"] == "plane" and d["fit"]["kind"] == "plane" and d["edge"] == []
    assert all(type(f) is int for f in d["faces"][:10]) and d["faces"] == sorted(d["faces"])


# --------------------------------------------------------------------------- speed
@pytest.mark.slow
def test_a_click_answers_fast_on_a_1m_triangle_mesh():
    """Worst cases: a plane that covers the whole 1M-triangle plate, a cylinder that covers a whole
    1M-triangle tube; and ordinary ones: a hole on the plate, a plane on one side of a 1M-triangle
    box. The measured times are in the module docstring of ohs.measure.region."""
    import scipy.sparse.csgraph  # noqa: F401  (a one-time import is not a click's cost)

    V, F = grid(709, 709, 0.25)
    V[:, :2] -= 88.5
    V, F = punch(V, F, [(30.0, 30.0, 0.0)], [12.5])
    assert len(F) > 980_000
    times = {}
    face, point = nearest_face(V, F, (-80.0, -80.0, 0.0))
    t0 = time.perf_counter()
    r = fit_at(V, F, face, point, "plane")
    times["plane"] = time.perf_counter() - t0
    assert len(r.faces) == len(F)
    face, point = nearest_face(V, F, (45.0, 30.0, 0.0))
    t0 = time.perf_counter()
    h = fit_at(V, F, face, point, "hole")
    times["hole"] = time.perf_counter() - t0
    assert abs(h.fit.diameter_mm - 25.0) < 0.25
    # a tube: r 20 mm, 1000 x 500 vertices, with 0.02 mm of noise like a scan's
    n, m = 1000, 500
    a = 2 * math.pi * np.arange(n) / n
    Vt = np.stack([np.tile(20 * np.cos(a), m), np.tile(20 * np.sin(a), m), np.repeat(np.arange(m) * 0.25, n)], 1)
    Vt += np.random.default_rng(8).normal(size=Vt.shape) * 0.02
    idx = np.arange(n * m).reshape(m, n)
    A, B = idx[:-1], np.roll(idx[:-1], -1, axis=1)
    C, D = np.roll(idx[1:], -1, axis=1), idx[1:]
    Ft = np.concatenate([np.stack([A, B, C], -1).reshape(-1, 3), np.stack([A, C, D], -1).reshape(-1, 3)])
    t0 = time.perf_counter()
    c = fit_at(Vt, Ft, 1234, Vt[Ft[1234]].mean(axis=0), "cylinder")
    times["cylinder"] = time.perf_counter() - t0
    assert len(c.faces) >= 0.97 * len(Ft) and abs(c.fit.radius_mm - 20.0) < 0.01
    # a region of ordinary size on a 1M-triangle mesh: one side of a box (1/6 of it)
    Vb, Fb = box(100.0, 289)
    assert len(Fb) > 1_000_000
    face, point = nearest_face(Vb, Fb, (10.0, 20.0, 50.0))
    t0 = time.perf_counter()
    b = fit_at(Vb, Fb, face, point, "plane")
    times["plane, one side of a box"] = time.perf_counter() - t0
    assert len(b.faces) == len(Fb) // 6
    print("click times (s):", {k: round(v, 3) for k, v in times.items()})
    # ordinary clicks well under a second; a region that is the whole 1M-triangle mesh within 3 s
    # (on the dev PC: about 0.8 s for the plate, 1.2-1.4 s for the tube, 0.4 s of it the final fit
    # on its 500k points), with room for a busy PC
    assert times["hole"] < 1.0 and times["plane, one side of a box"] < 1.0, times
    assert times["plane"] < 3.0 and times["cylinder"] < 3.0, times


def test_a_filled_pin_hole_does_not_hide_the_hole_next_to_it():
    """A plate with a Ø 25 hole and one missing triangle 2 mm outside its edge, closed by hole
    filling (as the Default recipe does). Filled faces are left out when holes are looked for, so
    the pin hole is a loop again, and nearer the click than the hole's edge: the hole is still found."""
    from ohs.mesh import carry_filled, fill_holes

    xs = np.arange(-40, 40 + 1e-9, 0.5)
    X, Y = np.meshgrid(xs, xs)
    V = np.column_stack([X.ravel(), Y.ravel(), np.zeros(X.size)])
    idx = np.arange(len(xs) ** 2).reshape(len(xs), len(xs))
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    F = np.concatenate([np.column_stack([a, b, d]), np.column_stack([a, d, c])])
    C = V[F].mean(axis=1)
    keep = np.hypot(C[:, 0], C[:, 1]) > 12.5
    keep[np.argmin(np.linalg.norm(C - [14.5, 0.0, 0.0], axis=1))] = False  # the pin hole
    F = F[keep]
    V2, F2 = fill_holes(V, F, 5.0)  # closes the pin hole, not the Ø 25 hole
    filled = carry_filled("fill_holes", V, F, None, V2, F2)
    assert filled.sum() >= 1
    C2 = V2[F2].mean(axis=1)
    click = np.array([14.0, 0.6, 0.0])
    face = int(np.argmin(np.where(filled, np.inf, np.linalg.norm(C2 - click, axis=1))))
    r = fit_at(V2, F2, face, C2[face], "hole", filled=filled)
    assert abs(r.fit.diameter_mm - 25.0) < 0.2 and np.linalg.norm(r.fit.center[:2]) < 0.1
