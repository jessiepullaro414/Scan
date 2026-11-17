"""ohs.measure.region: the extent of a fitted region (``RegionFit.extent``), on the region tests'
meshes with known truth.

* Plane: a box face gives its outline, its rectangle (20 x 20 mm) and its area to within a mesh
  edge; a plate with a hole gives an outer loop and one inner loop on the hole's circle.
* Cylinder: a boss of known height (8 mm) gives its span along the axis, all of its round; a
  quarter fillet gives about 90° of arc, drawn where the fillet is; the span equals the viewer's
  (``summarizeRegion``'s ``along``, the shared fixture ``tests/data/relations.json``).
* Hole: the edge as a closed loop of ordered points on the hole's circle, for an edge of the mesh
  and for a hole with a wall.
* ``to_dict(with_extent=True)`` is plain JSON; ``to_dict()`` is as before.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pytest

from mesh_shapes import box
from ohs.measure import fit_at, region_extent
from test_measure_region import BORE, BOSS, H, arc_wall, bracket, nearest_face, noisy, plate

Z = np.array([0.0, 0.0, 1.0])
FIXTURE = Path(__file__).parent / "data" / "relations.json"


@pytest.fixture(scope="module")
def bracket_mesh():
    V, F, labels = bracket()
    Vn, S = noisy(V, 1)
    return Vn, F, S, labels


def to_3d(ext: dict, loop) -> np.ndarray:
    """A plane outline's 2D points back in 3D: ``origin + x u + y v``."""
    xy = np.asarray(loop, float)
    return np.asarray(ext["origin"]) + xy[:, :1] * np.asarray(ext["u"]) + xy[:, 1:] * np.asarray(ext["v"])


def signed_area(loop) -> float:
    xy = np.asarray(loop, float)
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * float(x @ np.roll(y, -1) - y @ np.roll(x, -1))


# --------------------------------------------------------------------------- planes
def test_a_box_face_gives_its_outline_rectangle_and_area():
    size, n = 20.0, 32
    edge = size / n
    V, F = box(size, n)
    Vn, S = noisy(V, 2)
    face, point = nearest_face(Vn, F, (1.0, -2.0, 10.0))
    r = fit_at(Vn, F, face, point, "plane", sigma=S)
    ext = r.extent
    assert ext["kind"] == "plane"
    u, v, nrm = np.asarray(ext["u"]), np.asarray(ext["v"]), r.fit.normal
    assert np.allclose([u @ u, v @ v, u @ v, u @ nrm, v @ nrm], [1, 1, 0, 0, 0], atol=1e-12)
    assert np.allclose(np.cross(u, v), nrm, atol=1e-12) and np.allclose(ext["origin"], r.fit.point)
    assert len(ext["outer"]) == 1 and ext["holes"] == []
    outer = ext["outer"][0]
    assert signed_area(outer) > 0  # counter-clockwise seen from the normal's side
    P = to_3d(ext, outer)
    assert np.all(np.abs(P[:, 2] - 10.0) < 0.2)  # on the face, at its edge
    # at the face's edge; faces there that the region turned down in the noise leave a notch or two
    inset = 10.0 - np.maximum(np.abs(P[:, 0]), np.abs(P[:, 1]))
    assert np.mean(inset < edge) > 0.95 and inset.max() < 3 * edge
    rect = ext["rectangle"]
    a, b = rect["half_sizes_mm"]
    assert a >= b and abs(a - 10.0) < edge and abs(b - 10.0) < edge
    assert np.linalg.norm(np.asarray(rect["center"]) - (0.0, 0.0, 10.0)) < edge
    ax = np.asarray(rect["axes"])
    assert np.allclose(ax @ ax.T, np.eye(2), atol=1e-9) and np.all(np.abs(ax @ nrm) < 1e-9)
    assert max(abs(ax[0] @ (1, 0, 0)), abs(ax[0] @ (0, 1, 0))) > math.cos(math.radians(2.0))  # along the box's edges
    assert abs(ext["area_mm2"] - size * size) < 4 * size * edge  # within a mesh edge all round
    assert ext["area_mm2"] == pytest.approx(signed_area(outer), rel=0.01)


def test_a_plate_with_a_hole_gives_an_outer_loop_and_one_inner_loop():
    cx, cy, rad = 5.0, -3.0, 12.5
    V, F, _ = plate(80.0, 80.0, H, [(cx, cy, rad)])
    Vn, S = noisy(V, 5)
    face, point = nearest_face(Vn, F, (30.0, 30.0, 0.0))
    ext = fit_at(Vn, F, face, point, "plane", sigma=S).extent
    assert len(ext["outer"]) == 1 and len(ext["holes"]) == 1
    assert signed_area(ext["outer"][0]) > 0 and signed_area(ext["holes"][0]) < 0  # a hole runs the other way
    ring = to_3d(ext, ext["holes"][0])
    assert np.all(np.abs(np.hypot(ring[:, 0] - cx, ring[:, 1] - cy) - rad) < 0.2)
    a, b = ext["rectangle"]["half_sizes_mm"]
    assert abs(a - 40.0) < H and abs(b - 40.0) < H
    area = 80.0 * 80.0 - math.pi * rad * rad
    assert abs(ext["area_mm2"] - area) < (4 * 80.0 + 2 * math.pi * rad) * H
    assert ext["area_mm2"] == pytest.approx(signed_area(ext["outer"][0]) + signed_area(ext["holes"][0]), rel=0.01)


def best_time(f, runs: int = 3) -> float:
    t = []
    for _ in range(runs):
        t0 = time.perf_counter()
        f()
        t.append(time.perf_counter() - t0)
    return min(t)


def test_a_plane_with_hundreds_of_loops_costs_less_than_its_fit(monkeypatch):
    """A real scan's plane region has hundreds of loops (pits and specks the growth turned down; on a
    scanned table 722 of them after the specks were left out). Telling the holes from the outer loops
    by testing every loop against every other took 23 s there, against 1.2 s for the fit (now about
    0.12 s). Here: a plate with 529 small holes (Ø 3 mm on a 5 mm grid). The outline finds them all,
    and costs less than the fit itself (measured against the fit on the same mesh, so a slow PC
    slows both; the old way took 6.4 s against 0.17 s, now about 0.02 s)."""
    from ohs.measure import region

    xs = np.arange(-55.0, 55.0 + 1e-9, 5.0)
    circles = [(x, y, 1.5) for x in xs for y in xs]
    V, F, _ = plate(120.0, 120.0, H, circles)
    Vn, S = noisy(V, 12)
    face, point = nearest_face(Vn, F, (2.5, 2.5, 0.0))
    r = fit_at(Vn, F, face, point, "plane", sigma=S)
    ext = r.extent
    assert len(ext["outer"]) == 1 and len(ext["holes"]) == len(circles)
    a, b = ext["rectangle"]["half_sizes_mm"]
    assert abs(a - 60.0) < H and abs(b - 60.0) < H
    extent = best_time(lambda: region_extent(Vn, F, "plane", r.fit, r.faces))
    monkeypatch.setattr(region, "_extent", lambda *args: None)
    fit = best_time(lambda: fit_at(Vn, F, face, point, "plane", sigma=S))
    assert extent < fit, f"the extent took {extent:.3f} s, the fit {fit:.3f} s"


# --------------------------------------------------------------------------- cylinders
def test_a_boss_gives_its_height_and_all_its_round(bracket_mesh):
    V, F, S, _ = bracket_mesh
    x, y, radius, height = BOSS
    face, point = nearest_face(V, F, (x + radius * math.cos(1.0), y + radius * math.sin(1.0), 0.4 * height))
    r = fit_at(V, F, face, point, "cylinder", sigma=S)
    ext = r.extent
    assert ext["kind"] == "cylinder"
    lo, hi = ext["along_mm"]
    assert abs((hi - lo) - height) < H and ext["length_mm"] == pytest.approx(hi - lo)
    ends = sorted(float((r.fit.point + t * r.fit.axis)[2]) for t in (lo, hi))
    assert abs(ends[0]) < H and abs(ends[1] - height) < H  # from the plate up to the top
    assert ext["arc_deg"] == 360.0 and ext["start_deg"] == 0.0


def test_a_quarter_fillet_gives_about_90_degrees_of_arc_where_it_is():
    V, F = arc_wall(5.0, 90.0, 20.0, 0.25, noise=0.01, seed=3)
    S = np.full(len(V), 0.01)
    face, point = nearest_face(V, F, (5.0, 0.0, 10.0))
    r = fit_at(V, F, face, point, "cylinder", sigma=S)
    ext = r.extent
    step = math.degrees(0.25 / 5.0)
    assert abs(ext["arc_deg"] - 90.0) < step and abs(ext["length_mm"] - 20.0) < 0.25
    zero = np.asarray(ext["zero"])
    assert abs(zero @ r.fit.axis) < 1e-12 and abs(np.linalg.norm(zero) - 1.0) < 1e-12
    side = np.cross(r.fit.axis, zero)
    mid = math.radians(ext["start_deg"] + ext["arc_deg"] / 2)
    toward = math.cos(mid) * zero + math.sin(mid) * side  # the middle of the arc, drawn from the stated zero
    assert toward @ np.array([1.0, 0.0, 0.0]) > math.cos(math.radians(step))
    for end in (ext["start_deg"], ext["start_deg"] + ext["arc_deg"]):  # its ends are the wall's, at ±45°
        e = math.radians(end)
        d = math.cos(e) * zero + math.sin(e) * side
        assert abs(math.degrees(math.atan2(d[1], d[0])) % 90.0 - 45.0) < step


def test_the_span_along_the_axis_is_the_viewers():
    f = json.loads(FIXTURE.read_text(encoding="utf-8"))["along"]
    V = np.asarray(f["positions"], float).reshape(-1, 3)
    F = np.asarray(f["faces"], np.int64)
    s = f["shape"]
    from ohs.measure import CylinderFit

    fit = CylinderFit(point=np.asarray(s["point"]), axis=np.asarray(s["axis"]), radius_mm=s["radius_mm"], rms_mm=s["rms_mm"],
                      n=s["n"], radius_sigma_mm=s["radius_sigma_mm"], axis_sigma_deg=s["axis_sigma_deg"],
                      point_sigma_mm=s["point_sigma_mm"])
    ext = region_extent(V, F, "cylinder", fit, np.asarray(f["region"]))
    assert np.max(np.abs(np.asarray(ext["along_mm"]) - f["along"])) <= 1e-6


# --------------------------------------------------------------------------- holes
def check_loop(edge, center, normal, radius: float, tol: float, spacing: float) -> None:
    """``edge`` runs once round the circle, in order, and closes."""
    P = np.asarray(edge, float)
    q = P - center
    q = q - np.outer(q @ normal, normal)
    assert np.all(np.abs(np.linalg.norm(q, axis=1) - radius) < tol)
    gaps = np.linalg.norm(np.roll(P, -1, axis=0) - P, axis=1)  # the last point joins the first
    assert gaps.max() < spacing
    u = q[0] / np.linalg.norm(q[0])
    ang = np.unwrap(np.arctan2(q @ np.cross(normal, u), q @ u))
    turn = ang[-1] - ang[0] + (np.arctan2(np.sin(ang[0] - ang[-1]), np.cos(ang[0] - ang[-1])))
    assert abs(abs(turn) - 2 * math.pi) < 1e-6 and np.all(np.diff(ang) * np.sign(turn) > 0)


def test_a_hole_gives_its_edge_as_a_closed_loop_on_its_circle():
    V, F, (ring,) = plate(80.0, 80.0, H, [(5.0, -3.0, 12.5)])
    Vn, S = noisy(V, 5)
    face, point = nearest_face(Vn, F, (21.0, -3.0, 0.0))
    r = fit_at(Vn, F, face, point, "hole", sigma=S)
    ext = r.extent
    assert ext["kind"] == "hole" and len(ext["edge"]) == len(ring)
    assert np.allclose(ext["edge"], Vn[r.edge])
    check_loop(ext["edge"], np.array([5.0, -3.0, 0.0]), Z, 12.5, 0.2, 2 * H)


def test_a_hole_with_a_wall_gives_its_edge_too(bracket_mesh):
    V, F, S, _ = bracket_mesh
    qx, qy, qr, _ = BORE
    face, point = nearest_face(V, F, (qx + qr + 3.0, qy, 0.0))
    r = fit_at(V, F, face, point, "hole", sigma=S)
    check_loop(r.extent["edge"], np.array([qx, qy, 0.0]), Z, qr, 0.6, 3 * H)


# --------------------------------------------------------------------------- output
def test_the_extent_is_plain_json_and_only_on_request(bracket_mesh):
    V, F, S, _ = bracket_mesh
    for p, kind in (((0.0, 15.0, 0.0), "plane"), ((BOSS[0] + BOSS[2], 0.0, 3.0), "cylinder"),
                    ((BORE[0] + BORE[2] + 3.0, 0.0, 0.0), "hole")):
        face, point = nearest_face(V, F, p)
        r = fit_at(V, F, face, point, kind, sigma=S)
        d = r.to_dict(with_extent=True)
        assert json.loads(json.dumps(d)) == d and d["extent"]["kind"] == kind
        assert "extent" not in r.to_dict()
