"""ohs.cad.moves: an approved relation moves one of its two shapes in the CAD export, when the
user chooses so (the owner's decision after FEATURE_PLAN.md C3).

* Each kind of move on shapes whose truth is known: two planes 0.3° apart made parallel by turning
  the second about its own point (the first untouched, the relation read again with ``relate``
  gives 0 to 1e-9); a distance set by sliding along the normal; perpendicular; coaxial (a turn
  and a slide); a plane and an axis (the axis moved, or the plane); two holes' centre distance.
* The order rule: moves are applied in the order given (the order they were chosen); a shape is
  moved by one relation only; a shape another applied relation was measured against stays where
  it is. What is refused is not applied, and says why in plain words.
* The measured records given in are never changed; a moved plane's extent and outline go with it.
"""
from __future__ import annotations

import copy
import math

import numpy as np
import pytest

from ohs.cad.moves import MOVE_TOL, apply_moves
from ohs.measure.relate import relate

TOL = 1e-9


# --------------------------------------------------------------------------- helpers
def unit(v) -> list[float]:
    v = np.asarray(v, dtype=float)
    return [float(x) for x in v / np.linalg.norm(v)]


def tilted(deg: float, about: str = "y") -> list[float]:
    """The z direction turned by ``deg`` about the y axis (towards +x), or about x (towards -y)."""
    t = math.radians(deg)
    return [math.sin(t), 0.0, math.cos(t)] if about == "y" else [0.0, -math.sin(t), math.cos(t)]


def plane(fid: str, normal, point, extent: bool = True) -> dict:
    n = unit(normal)
    u = unit(np.cross(n, [0.0, 1.0, 0.0])) if abs(n[1]) < 0.9 else unit(np.cross(n, [1.0, 0.0, 0.0]))
    v = unit(np.cross(n, u))
    shape = {"kind": "plane", "point": list(point), "normal": n, "rms_mm": 0.02, "flatness_mm": 0.1, "n": 900,
             "normal_sigma_deg": 0.05, "offset_sigma_mm": 0.01}
    ext = {"kind": "plane", "origin": list(point), "u": u, "v": v,
           "rectangle": {"center": [p + 3.0 * a for p, a in zip(point, u)], "axes": [u, v], "half_sizes_mm": [20.0, 15.0]},
           "area_mm2": 1200.0, "n_outer": 1, "n_holes": 0, "n_points": 4} if extent else None
    return {"id": fid, "name": fid.capitalize(), "kind": "plane", "shape": shape, "extent": ext, "warnings": []}


def cylinder(fid: str, axis, point, d: float = 14.0) -> dict:
    a = unit(axis)
    zero = unit(np.cross(a, [0.0, 1.0, 0.0])) if abs(a[1]) < 0.9 else unit(np.cross(a, [1.0, 0.0, 0.0]))
    shape = {"kind": "cylinder", "point": list(point), "axis": a, "radius_mm": d / 2, "diameter_mm": d, "rms_mm": 0.02,
             "n": 800, "radius_sigma_mm": 0.01, "diameter_sigma_mm": 0.02, "axis_sigma_deg": 0.3, "point_sigma_mm": 0.01}
    ext = {"kind": "cylinder", "along_mm": [-5.0, 5.0], "length_mm": 10.0, "zero": zero, "start_deg": 0.0,
           "arc_deg": 360.0}
    return {"id": fid, "name": fid.capitalize(), "kind": "cylinder", "shape": shape, "extent": ext, "warnings": []}


def hole(fid: str, normal, center, d: float = 10.0) -> dict:
    shape = {"kind": "circle", "center": list(center), "normal": unit(normal), "radius_mm": d / 2, "diameter_mm": d,
             "rms_mm": 0.02, "n": 200, "radius_sigma_mm": 0.01, "diameter_sigma_mm": 0.02, "center_sigma_mm": 0.02}
    ext = {"kind": "hole", "n_edge": 2, "edge": [[center[0] + d / 2, center[1], center[2]],
                                                 [center[0] - d / 2, center[1], center[2]]]}
    return {"id": fid, "name": fid.capitalize(), "kind": "hole", "shape": shape, "extent": ext, "warnings": []}


def rel(rid: str, a: str, b: str, move: str, **values) -> dict:
    """A kept relation with a move chosen and approved values (``angle=0``, ``distance=10`` ...)."""
    words = {("angle", 0.0): "parallel", ("angle", 90.0): "perpendicular"}
    nominals = [{"value": k, "nominal": float(v), "unit": "deg" if k == "angle" else "mm",
                 "word": words.get((k, float(v))), "check_again": False} for k, v in values.items()]
    return {"id": rid, "name": rid.upper(), "a": a, "b": b, "move": move, "nominals": nominals}


def by_id(features: list[dict], fid: str) -> dict:
    return next(f for f in features if f["id"] == fid)


def related(features: list[dict], a: str, b: str):
    fa, fb = by_id(features, a), by_id(features, b)
    return relate({"kind": fa["kind"], "shape": fa["shape"]}, {"kind": fb["kind"], "shape": fb["shape"]})


def value(r, name: str) -> float:
    q = r.get(name)
    assert q is not None, (name, r.to_dict())
    return q.value


def angle_between(u, v) -> float:
    u, v = np.asarray(u, float), np.asarray(v, float)
    return math.degrees(math.atan2(np.linalg.norm(np.cross(u, v)), float(u @ v)))


def run(features, relations, outlines=None):
    before = copy.deepcopy((features, relations, outlines))
    out = apply_moves(features, relations, outlines)
    assert (features, relations, outlines) == before, "the measured records were changed"
    return out


# --------------------------------------------------------------------------- each kind of move
def test_two_planes_0_3_degrees_apart_made_parallel_by_turning_the_second():
    a = plane("top", [0, 0, 1], [0.0, 0.0, 10.0])
    b = plane("bottom", tilted(0.3), [1.0, 2.0, -0.5])
    out = run([a, b], [rel("r1", "top", "bottom", "b", angle=0)])
    (m,) = out.moves
    assert m.applied and m.reason is None and m.moved_id == "bottom" and m.fixed_id == "top"
    assert by_id(out.features, "top") == a  # the first is untouched
    nb = by_id(out.features, "bottom")["shape"]
    assert np.allclose(nb["point"], b["shape"]["point"], atol=1e-12)  # turned about its own point
    assert angle_between(nb["normal"], b["shape"]["normal"]) == pytest.approx(0.3, abs=1e-9)
    assert m.turn_deg == pytest.approx(0.3, abs=1e-9) and m.slide_mm == pytest.approx(0.0, abs=1e-12)
    assert value(related(out.features, "top", "bottom"), "angle") == pytest.approx(0.0, abs=TOL)
    assert m.holds and all(abs(v["exported"] - v["nominal"]) <= MOVE_TOL for v in m.values)
    assert "turned 0.3" in m.sentence and "Bottom" in m.sentence and "Top" in m.sentence


def test_a_distance_is_set_by_sliding_along_the_normal():
    a = plane("top", [0, 0, 1], [0.0, 0.0, 10.03])
    b = plane("bottom", [0, 0, 1], [5.0, -3.0, 0.0])
    out = run([a, b], [rel("r1", "top", "bottom", "b", distance=10)])
    (m,) = out.moves
    assert m.applied and m.turn_deg == pytest.approx(0.0, abs=1e-12)
    nb = by_id(out.features, "bottom")["shape"]
    assert np.allclose(nb["point"], [5.0, -3.0, 0.03], atol=1e-12) and nb["normal"] == b["shape"]["normal"]
    assert m.slide_mm == pytest.approx(0.03, abs=1e-12)
    assert value(related(out.features, "top", "bottom"), "distance") == pytest.approx(10.0, abs=TOL)
    # the other way: the first is moved, the second stays
    out = run([a, b], [rel("r1", "top", "bottom", "a", distance=10)])
    assert by_id(out.features, "bottom") == b
    assert np.allclose(by_id(out.features, "top")["shape"]["point"], [0.0, 0.0, 10.0], atol=1e-12)


def test_parallel_and_a_distance_together_turn_then_slide():
    a = plane("top", [0, 0, 1], [0.0, 0.0, 10.0])
    b = plane("bottom", tilted(0.4), [0.0, 0.0, 0.05])
    out = run([a, b], [rel("r1", "top", "bottom", "b", angle=0, distance=10)])
    (m,) = out.moves
    assert m.applied
    r = related(out.features, "top", "bottom")
    assert value(r, "angle") == pytest.approx(0.0, abs=TOL) and value(r, "distance") == pytest.approx(10.0, abs=TOL)
    assert m.turn_deg == pytest.approx(0.4, abs=1e-9) and m.slide_mm == pytest.approx(0.05, abs=1e-9)
    # "in one plane": both values at 0
    out = run([a, b], [rel("r1", "top", "bottom", "b", angle=0, distance=0)])
    r = related(out.features, "top", "bottom")
    assert value(r, "angle") == pytest.approx(0.0, abs=TOL) and value(r, "distance") == pytest.approx(0.0, abs=TOL)


def test_perpendicular_turns_the_chosen_shape_the_least():
    a = plane("side", tilted(89.7), [0.0, 0.0, 0.0])  # 89.7° from the floor's normal
    b = plane("floor", [0, 0, 1], [0.0, 0.0, -20.0])
    out = run([a, b], [rel("r1", "side", "floor", "a", angle=90)])
    (m,) = out.moves
    assert m.applied and m.moved_id == "side" and by_id(out.features, "floor") == b
    assert m.turn_deg == pytest.approx(0.3, abs=1e-9)
    assert value(related(out.features, "side", "floor"), "angle") == pytest.approx(90.0, abs=TOL)
    n = by_id(out.features, "side")["shape"]["normal"]
    assert angle_between(n, a["shape"]["normal"]) == pytest.approx(0.3, abs=1e-9)


def test_two_axes_made_coaxial():
    a = cylinder("shaft", [0, 0, 1], [0.0, 0.0, 0.0])
    b = cylinder("bore", tilted(0.2), [0.04, -0.03, 8.0], d=20.0)
    out = run([a, b], [rel("r1", "shaft", "bore", "b", angle=0, distance=0)])
    (m,) = out.moves
    assert m.applied and by_id(out.features, "shaft") == a
    r = related(out.features, "shaft", "bore")
    assert value(r, "angle") == pytest.approx(0.0, abs=TOL) and value(r, "distance") == pytest.approx(0.0, abs=TOL)
    assert m.turn_deg == pytest.approx(0.2, abs=1e-9) and m.slide_mm == pytest.approx(0.05, abs=1e-3)
    nb = by_id(out.features, "bore")
    # the extent's zero direction is turned with the axis: still square to it
    assert abs(np.dot(nb["extent"]["zero"], nb["shape"]["axis"])) < 1e-12


def test_a_plane_and_an_axis_either_one_moves():
    floor = plane("floor", [0, 0, 1], [0.0, 0.0, 0.0])
    pin = cylinder("pin", [1.0, 0.0, math.tan(math.radians(0.4))], [10.0, 0.0, 5.02])
    rels = [rel("r1", "floor", "pin", "b", angle=0, distance=5)]  # the axis along the plane, 5 mm above it
    out = run([floor, pin], rels)
    (m,) = out.moves
    assert m.applied and by_id(out.features, "floor") == floor
    r = related(out.features, "floor", "pin")
    assert value(r, "angle") == pytest.approx(0.0, abs=TOL) and value(r, "distance") == pytest.approx(5.0, abs=TOL)
    assert m.turn_deg == pytest.approx(0.4, abs=1e-9)
    # the plane moves instead: the pin stays
    out = run([floor, pin], [rel("r1", "floor", "pin", "a", angle=0, distance=5)])
    assert by_id(out.features, "pin") == pin
    r = related(out.features, "floor", "pin")
    assert value(r, "angle") == pytest.approx(0.0, abs=TOL) and value(r, "distance") == pytest.approx(5.0, abs=TOL)
    # a hole square to a plane: "perpendicular" turns the hole's normal along the plane's
    h = hole("hole", tilted(0.5, "x"), [3.0, 4.0, 0.1])
    out = run([floor, h], [rel("r2", "floor", "hole", "b", angle=90, distance=0)])
    (m,) = out.moves
    assert m.applied
    r = related(out.features, "floor", "hole")
    assert value(r, "angle") == pytest.approx(90.0, abs=TOL) and value(r, "distance") == pytest.approx(0.0, abs=TOL)
    nh = by_id(out.features, "hole")
    assert np.allclose(nh["shape"]["center"], [3.0, 4.0, 0.0], atol=1e-12)


def test_two_holes_centre_distance_slides_along_the_line_of_centres():
    a = hole("left", [0, 0, 1], [0.0, 0.0, 0.0])
    b = hole("right", [0, 0, 1], [30.04, 0.0, 0.0])
    out = run([a, b], [rel("r1", "left", "right", "b", centre_distance=30)])
    (m,) = out.moves
    assert m.applied and np.allclose(by_id(out.features, "right")["shape"]["center"], [30.0, 0.0, 0.0], atol=1e-12)
    assert value(related(out.features, "left", "right"), "centre_distance") == pytest.approx(30.0, abs=TOL)
    # with the distance across the axes approved too: that first, then along the axes
    b2 = hole("right", [0, 0, 1], [30.04, 0.0, 2.0])
    out = run([a, b2], [rel("r1", "left", "right", "b", distance=30, centre_distance=31)])
    (m,) = out.moves
    r = related(out.features, "left", "right")
    assert m.applied and value(r, "distance") == pytest.approx(30.0, abs=TOL)
    assert value(r, "centre_distance") == pytest.approx(31.0, abs=TOL)
    # a centre distance shorter than the distance across the axes cannot be reached
    out = run([a, b2], [rel("r1", "left", "right", "b", distance=30, centre_distance=29)])
    (m,) = out.moves
    assert not m.applied and "cannot be reached" in m.reason
    assert by_id(out.features, "right") == b2


def test_a_moved_plane_takes_its_extent_and_outline_along():
    a = plane("top", [0, 0, 1], [0.0, 0.0, 10.0])
    b = plane("bottom", tilted(1.0), [0.0, 0.0, 0.0])
    ext = b["extent"]
    outline = {"kind": "plane", "origin": ext["origin"], "u": ext["u"], "v": ext["v"], "mesh_version": 1,
               "outer": [[[-5.0, -5.0], [5.0, -5.0], [5.0, 5.0], [-5.0, 5.0]]], "holes": []}
    out = run([a, b], [rel("r1", "top", "bottom", "b", angle=0, distance=10)], {"bottom": outline})
    nb = by_id(out.features, "bottom")
    n, p = np.asarray(nb["shape"]["normal"]), np.asarray(nb["shape"]["point"])
    for q in (nb["extent"]["origin"], nb["extent"]["rectangle"]["center"], out.outlines["bottom"]["origin"]):
        assert abs(float(n @ (np.asarray(q) - p))) < 1e-12  # on the moved plane
    for d in (nb["extent"]["u"], nb["extent"]["v"], *nb["extent"]["rectangle"]["axes"], out.outlines["bottom"]["u"]):
        assert abs(float(n @ np.asarray(d))) < 1e-12  # along it
    assert out.outlines["bottom"]["outer"] == outline["outer"]  # 2D in its own basis: the same loops


# --------------------------------------------------------------------------- order and conflicts
def test_moves_follow_the_order_given_and_never_spoil_an_earlier_one():
    a = plane("a", [0, 0, 1], [0.0, 0.0, 0.0])
    b = plane("b", tilted(0.3), [0.0, 0.0, 10.0])
    c = plane("c", tilted(-0.2), [0.0, 0.0, 20.0])
    rels = [rel("r1", "a", "b", "b", angle=0),  # B turned to suit A
            rel("r2", "c", "b", "b", angle=0),  # B again: refused, a shape is moved by one relation only
            rel("r3", "a", "c", "a", angle=0),  # A was held for r1: refused
            rel("r4", "c", "b", "b", angle=0)]  # B once more: refused again
    out = run([a, b, c], rels)
    m1, m2, m3, m4 = out.moves
    assert m1.applied
    assert not m2.applied and "already moved" in m2.reason and "R1" in m2.reason
    assert not m3.applied and "would undo" in m3.reason and "R1" in m3.reason
    assert not m4.applied and "already moved" in m4.reason
    assert by_id(out.features, "a") == a and by_id(out.features, "c") == c
    assert value(related(out.features, "a", "b"), "angle") == pytest.approx(0.0, abs=TOL)
    # C moved to suit B as exported (B was moved by r1, and C is held by nothing): allowed, and both hold
    out = run([a, b, c], [rel("r1", "a", "b", "b", angle=0), rel("r6", "b", "c", "b", angle=0)])
    m1, m6 = out.moves
    assert m1.applied and m6.applied and m6.moved_id == "c"
    assert value(related(out.features, "a", "b"), "angle") == pytest.approx(0.0, abs=TOL)
    assert value(related(out.features, "b", "c"), "angle") == pytest.approx(0.0, abs=TOL)
    assert m1.holds and m6.holds


def test_what_cannot_be_applied_says_why_and_moves_nothing():
    a = plane("a", [0, 0, 1], [0.0, 0.0, 0.0])
    b = plane("b", [0, 0, 1], [0.0, 0.0, 0.0])  # in one plane already
    out = run([a, b], [rel("r1", "a", "b", "b", distance=3)])
    (m,) = out.moves
    assert not m.applied and "which side" in m.reason and by_id(out.features, "b") == b
    # exactly parallel directions cannot be turned to 30° in one nearest way
    out = run([a, b], [rel("r1", "a", "b", "b", angle=30)])
    (m,) = out.moves
    assert not m.applied and "no one nearest way" in m.reason
    # planes turned apart have no one distance: the distance is not set and nothing moves
    c = plane("c", tilted(1.0), [0.0, 0.0, 10.0])
    out = run([a, c], [rel("r1", "a", "c", "b", angle=20, distance=10)])
    (m,) = out.moves
    assert not m.applied and "no one distance" in m.reason and by_id(out.features, "c") == c
    # a fit that is not there any more
    out = run([a], [rel("r1", "a", "gone", "b", angle=0)])
    (m,) = out.moves
    assert not m.applied and "not kept" in m.reason


def test_an_approval_marked_check_again_is_applied_with_a_warning():
    a = plane("top", [0, 0, 1], [0.0, 0.0, 10.0])
    b = plane("bottom", tilted(1.0), [0.0, 0.0, 0.0])
    r = rel("r1", "top", "bottom", "b", angle=0)
    r["nominals"][0]["check_again"] = True
    out = run([a, b], [r])
    (m,) = out.moves
    assert m.applied and m.warnings and "check again" in m.warnings[0]
    assert value(related(out.features, "top", "bottom"), "angle") == pytest.approx(0.0, abs=TOL)


def test_listed_only_and_relations_without_approvals_move_nothing():
    a = plane("top", [0, 0, 1], [0.0, 0.0, 10.0])
    b = plane("bottom", tilted(0.3), [0.0, 0.0, 0.0])
    listed = rel("r1", "top", "bottom", "b", angle=0)
    del listed["move"]
    empty = rel("r2", "top", "bottom", "a")
    out = run([a, b], [listed, empty])
    assert out.moves == [] and out.features == [a, b]
    with pytest.raises(ValueError):
        apply_moves([a, b], [rel("r1", "top", "bottom", "c", angle=0)])
