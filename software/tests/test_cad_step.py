"""``ohs.cad.shapes`` and ``ohs.cad.step`` (FEATURE_PLAN C1): kept features written as a STEP file.

The files are read back with a small reader of the tests' own (``tests/step_reader.py``): every
reference resolves, the units are mm, and each plane, cylinder, circle, line and point equals the
feature's numbers to 1e-6 mm, in the part's frame when there is one. The last test opens the
sample files (``tools/step_samples.py``) in FreeCAD's command-line program with
``tools/freecad_check.py`` and compares what FreeCAD reports; it is skipped when FreeCAD is not
installed.
"""
from __future__ import annotations

import glob
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from step_reader import missing_references, read  # noqa: E402
from tools import step_samples  # noqa: E402

from ohs.cad import shapes as cs  # noqa: E402
from ohs.cad import step  # noqa: E402
from ohs.measure.frame import PartFrame  # noqa: E402

TOL = 1e-6  # mm, and for unit directions
TOOLS = Path(__file__).resolve().parents[1] / "tools"


# --------------------------------------------------------------------------- helpers
def _close(a, b, tol=TOL):
    assert np.allclose(np.asarray(a, float), np.asarray(b, float), atol=tol, rtol=0), (a, b)


def _parallel(a, b, tol=TOL):
    a, b = np.asarray(a, float), np.asarray(b, float)
    assert np.linalg.norm(np.cross(a / np.linalg.norm(a), b / np.linalg.norm(b))) < tol, (a, b)


def _one(items, kind):
    found = [x for x in items if x["type"] == kind]
    assert len(found) == 1, (kind, [x["type"] for x in items])
    return found[0]


def _export(features, outlines=None, frame=None, **kw):
    shapes = cs.cad_shapes(features, outlines=outlines, frame=frame, **kw)
    text = step.step_text(shapes, title="Test part")
    return shapes, text, read(text)


def _box():
    """The box sample's features, outlines and frame (``tools/step_samples.py``)."""
    s = step_samples.sample_set()["box_with_hole_and_boss.step"]
    return s["features"], s["outlines"], s["frame"]


def _by_id(features, fid):
    return next(f for f in features if f["id"] == fid)


# --------------------------------------------------------------------------- the file
def test_references_resolve_and_units_are_mm():
    features, outlines, frame = _box()
    _, text, r = _export(features, outlines, frame)
    assert missing_references(r["file"]) == []
    assert r["schema"] == ["AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }"]
    assert r["units"] == {("MILLI", "METRE")}
    assert r["root"] == "Test part"
    assert text.isascii()


def test_box_in_its_part_frame():
    features, outlines, frame = _box()
    pf = PartFrame.from_dict(frame)
    _, _, r = _export(features, outlines, frame)
    products = r["products"]
    # the origin hole is at 0, 0, 0
    hole = _by_id(features, "f-hole")
    circle = _one(products[hole["name"]], "circle")
    _close(circle["center"], [0, 0, 0])
    _close(circle["radius"], hole["shape"]["radius_mm"])
    want = pf.fit_to_part(hole["shape"])
    _close(circle["normal"], want["normal"])
    assert circle["name"] == hole["name"]
    axis_line = _one(products[hole["name"] + " axis"], "line")
    _parallel(axis_line["end"] - axis_line["start"], want["normal"])
    _close(0.5 * (axis_line["start"] + axis_line["end"]), [0, 0, 0])
    # planes: point and normal as the features', in the part's frame
    for fid in ("f-top", "f-side"):
        f = _by_id(features, fid)
        want = pf.fit_to_part(f["shape"])
        face = _one(products[f["name"]], "plane")
        _close(face["point"], want["point"])
        _close(face["normal"], want["normal"])
        assert face["loop_kinds"] == ["outer"] and len(face["loops"][0]) == 4  # the rectangle
        # every corner on the plane, and the rectangle's size as the extent's
        _close((face["loops"][0] - want["point"]) @ np.asarray(want["normal"]), np.zeros(4))
        sides = np.linalg.norm(np.diff(np.vstack([face["loops"][0], face["loops"][0][:1]]), axis=0), axis=1)
        half = f["extent"]["rectangle"]["half_sizes_mm"]
        _close(sorted(sides), sorted([2 * half[0], 2 * half[0], 2 * half[1], 2 * half[1]]))
    # the boss: a full round, its axis over its span
    boss = _by_id(features, "f-boss")
    want = pf.fit_to_part(boss["shape"])
    face = _one(products[boss["name"]], "cylinder")
    _close(face["radius"], boss["shape"]["radius_mm"])
    _parallel(face["axis"], want["axis"])
    _close(np.cross(face["point"] - want["point"], want["axis"]), [0, 0, 0])
    line = _one(products[boss["name"] + " axis"], "line")
    assert line["name"] == boss["name"] + " axis"
    lo, hi = boss["extent"]["along_mm"]
    a = np.asarray(want["axis"])
    _close(line["start"], np.asarray(want["point"]) + lo * a)
    _close(line["end"], np.asarray(want["point"]) + hi * a)
    # the part's axes on the CAD origin
    for name, d in (("X axis", [1, 0, 0]), ("Y axis", [0, 1, 0]), ("Z axis", [0, 0, 1])):
        ln = _one(products[name], "line")
        _close(ln["start"], [0, 0, 0])
        _parallel(ln["end"] - ln["start"], d)
        assert (ln["end"] - ln["start"]) @ d > 0
    _close(_one(products["Part origin"], "point")["point"], [0, 0, 0])


def test_scan_frame_without_a_part_frame():
    features, outlines, _ = _box()
    shapes, _, r = _export(features, outlines, None)
    assert not shapes.in_part_frame and shapes.axes is None
    assert any("scan's own frame" in n for n in shapes.notes)
    assert "X axis" not in r["products"]
    hole = _by_id(features, "f-hole")
    circle = _one(r["products"][hole["name"]], "circle")
    _close(circle["center"], hole["shape"]["center"])
    top = _by_id(features, "f-top")
    face = _one(r["products"][top["name"]], "plane")
    _close(face["point"], top["shape"]["point"])
    _close(face["normal"], top["shape"]["normal"])


def test_same_features_give_byte_identical_files(tmp_path):
    features, outlines, frame = _box()
    a = step.step_text(cs.cad_shapes(features, outlines=outlines, frame=frame), title="Box")
    features2, outlines2, frame2 = _box()  # built again from scratch
    b = step.step_text(cs.cad_shapes(features2, outlines=outlines2, frame=frame2), title="Box")
    assert a == b
    p1, p2 = tmp_path / "a.step", tmp_path / "b.step"
    step.write_step(p1, cs.cad_shapes(features, outlines=outlines, frame=frame), title="Box")
    step.write_step(p2, cs.cad_shapes(features2, outlines=outlines2, frame=frame2), title="Box")
    assert p1.read_bytes() == p2.read_bytes() == a.encode("ascii")
    assert "FILE_NAME('Box',''," in a  # no time stamp unless one is passed in
    assert "FILE_NAME('Box','2026-11-17T10:00:00'," in step.step_text(
        cs.cad_shapes(features, frame=frame), title="Box", timestamp="2026-11-17T10:00:00")


def test_unusual_names_are_escaped_and_read_back():
    features, outlines, frame = _box()
    odd = "Bob's \"top\" face \\ Ø 14 – über 日本 😀"
    for f in features:
        if f["id"] == "f-top":
            f["name"] = odd
    _, text, r = _export(features, outlines, frame)
    assert text.isascii()
    assert odd in r["products"]
    face = _one(r["products"][odd], "plane")
    assert face["name"] == odd


# --------------------------------------------------------------------------- outlines
def _jagged_plate(rng, n=40000):
    """A 200 x 120 mm plate outline with mesh-like steps (0.4 mm from side to side) and a Ø 20 mm hole,
    plus a pit (the loops as a mesh's border has them: they do not cross themselves)."""
    outer = step_samples._stepped_loop(rng, ("poly", [[0, 0], [200, 0], [200, 120], [0, 120]]), n, 0.2)
    hole = step_samples._stepped_loop(rng, ("circle", ((60, 60), 10.0)), 3000, 0.15)[::-1]
    pit = step_samples._stepped_loop(rng, ("circle", ((150, 30), 0.4)), 12, 0.02)[::-1]
    return outer, [hole, pit]


def _area(xy):
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - y * np.roll(x, -1)))


def _perimeter(xy):
    return float(np.linalg.norm(np.roll(xy, -1, axis=0) - xy, axis=1).sum())


def test_simplified_outline_keeps_the_area_and_the_point_limit():
    rng = np.random.default_rng(1)
    outer, holes = _jagged_plate(rng)
    out, kept, tol, notes = cs.simplify_outline([outer], holes)
    assert tol == cs.OUTLINE_TOLERANCE_MM
    assert notes == ["1 of its 2 holes are smaller than 5.24 mm across (3% of the outline's size, and at least 2 mm), "
                     "or outside its largest piece: left out as pits of the scan"]
    assert cs.face_problems(out, kept) == []
    assert len(out) + sum(len(h) for h in kept) <= cs.OUTLINE_MAX_POINTS
    assert len(kept) == 1  # the Ø 20 hole; the Ø 0.8 pit is no real hole
    assert abs(_area(out) - _area(outer)) <= tol * _perimeter(outer)
    assert abs(_area(kept[0]) - _area(holes[0])) <= tol * _perimeter(holes[0])
    assert _area(out) > 0 and _area(kept[0]) < 0  # outer counter-clockwise, holes clockwise
    assert len(out) < len(outer) / 20


def test_the_tolerance_grows_to_its_cap_and_no_further():
    rng = np.random.default_rng(2)
    ang = np.linspace(0, 2 * np.pi, 20000, endpoint=False)
    # a wavy edge, thousands of corners: 0.8 mm waves fit 300 points at a larger tolerance
    r = 50 + 0.8 * np.sin(400 * ang) + rng.uniform(-0.1, 0.1, len(ang))
    outer = np.c_[r * np.cos(ang), r * np.sin(ang)]
    out, _, tol, notes = cs.simplify_outline([outer], [], max_points=300)
    assert out is not None and len(out) <= 300 and cs.face_problems(out, []) == []
    assert cs.OUTLINE_TOLERANCE_MM < tol <= cs.OUTLINE_TOLERANCE_CAP * cs.OUTLINE_TOLERANCE_MM
    assert notes == [f"the outline was simplified to {tol:g} mm (not 0.5 mm) to keep within 300 points"]
    assert abs(_area(out) - _area(outer)) <= tol * _perimeter(outer)
    # 3 mm waves would need more than the cap: no outline then (the caller writes the rectangle)
    r = 50 + 3 * np.sin(400 * ang)
    out, _, tol, notes = cs.simplify_outline([np.c_[r * np.cos(ang), r * np.sin(ang)]], [], max_points=300)
    assert out is None and tol == cs.OUTLINE_TOLERANCE_CAP * cs.OUTLINE_TOLERANCE_MM
    assert "more than 300" in notes[-1]


def test_simplify_loop_keeps_every_point_within_the_tolerance():
    rng = np.random.default_rng(3)
    outer, _ = _jagged_plate(rng, 8000)
    out = cs.simplify_loop(outer, 0.5)
    # every original point lies within the tolerance of the simplified loop
    a, b = out, np.roll(out, -1, axis=0)
    d = b - a
    w = outer[:, None, :] - a[None]
    t = np.clip(np.einsum("nkj,kj->nk", w, d) / np.einsum("kj,kj->k", d, d), 0, 1)
    dist = np.linalg.norm(w - t[..., None] * d[None], axis=2).min(axis=1)
    assert dist.max() <= 0.5 + 1e-9


# --------------------------------------------------------------------------- a valid face, always
def test_the_validity_check_finds_what_cad_refuses():
    square = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    assert cs.loop_problem(square) is None
    assert cs.loop_problem(np.array([[0, 0], [10, 10], [10, 0], [0, 10]], float)) == "it crosses or touches itself"
    assert cs.loop_problem(np.array([[0, 0], [10, 0], [5, 0], [5, 5]], float)) == "it folds back on itself"
    assert cs.loop_problem(np.array([[0, 0], [10, 0], [10, 0], [0, 10]], float)) == "two of its points coincide"
    # a figure of eight that touches itself at one point
    eight = np.array([[0, 0], [5, 5], [10, 0], [10, 10], [5, 5], [0, 10]], float)
    assert cs.loop_problem(eight) == "it crosses or touches itself"

    def hole(cx, cy, r):
        a = np.linspace(0, 2 * np.pi, 24, endpoint=False)[::-1]
        return np.c_[cx + r * np.cos(a), cy + r * np.sin(a)]

    assert cs.face_problems(square, [hole(5, 5, 2)]) == []
    assert cs.face_problems(square, [hole(5, 5, 2)[::-1]]) == ["hole 1 does not turn clockwise"]
    assert cs.face_problems(square, [hole(9, 5, 2)]) == ["hole 1 crosses or touches the outer loop"]
    assert cs.face_problems(square, [hole(20, 5, 2)]) == ["hole 1 lies outside the outer loop"]
    assert cs.face_problems(square, [hole(3, 5, 1.5), hole(5, 5, 1.5)]) == ["holes 1 and 2 cross or touch"]
    assert cs.face_problems(square, [hole(5, 5, 3), hole(5, 5, 1)]) == ["holes 1 and 2 lie one inside the other"]
    assert cs.face_problems(square, [hole(5, 5 + 2.0005, 2), hole(5, 5 - 2.0, 2)])  # 0.0005 mm apart: touching


def test_a_loop_broken_by_simplifying_gets_its_points_back():
    # a hooked inlet from the top; leaving out two of its corners makes an edge cross another one
    raw = np.array([[0, 0], [10, 0], [10, 10], [8, 10], [8, 4], [2, 4], [2, 6], [6, 6], [6, 10], [0, 10]], float)
    keep = np.ones(len(raw), dtype=bool)
    keep[[4, 5]] = False
    assert cs.loop_problem(raw[keep]) == "it crosses or touches itself"
    fixed = cs._simple_loop(raw, keep, cs.MIN_GAP_MM)
    assert fixed is not None and cs.loop_problem(fixed) is None
    assert len(fixed) == len(raw)  # the stretch put back is the measured border itself
    # a loop that crosses itself where it was measured cannot be repaired
    bow = np.array([[0, 0], [10, 10], [10, 0], [0, 10]], float)
    assert cs._simple_loop(bow, np.ones(4, dtype=bool), cs.MIN_GAP_MM) is None


def test_ragged_table_outline_is_a_valid_face():
    """As hard as a real scan's table top: a ragged outer loop of 28,000 points with slots and a lobe
    that touches it, islands beside it, 300 pits of 2-6 mm and two real holes."""
    s = step_samples.sample_set()["ragged_table.step"]
    ol = s["outlines"]["f-table"]
    assert len(ol["outer"][0]) > 20000 and len(ol["outer"]) == 5 and len(ol["holes"]) == 302
    out, holes, tol, notes = cs.simplify_outline(ol["outer"], ol["holes"])
    assert out is not None
    assert cs.face_problems(out, holes) == []
    assert tol <= cs.OUTLINE_TOLERANCE_CAP * cs.OUTLINE_TOLERANCE_MM
    assert len(out) + sum(len(h) for h in holes) <= cs.OUTLINE_MAX_POINTS
    # the two real holes survive (as circles of the same area), the pits do not
    diameters = sorted(2 * math.sqrt(abs(_area(h)) / math.pi) for h in holes)
    want = sorted(d for _, d in step_samples.TABLE_HOLES)
    assert len(diameters) == 2 and np.allclose(diameters, want, atol=1.0)
    assert any("300 of its 302 holes" in n for n in notes)
    assert any("touched itself at 1 points" in n for n in notes)
    # the loops lie where the outline's do: the area within the tolerance times the perimeter
    piece = cs._split_pinches(np.asarray(ol["outer"][0]))[0]
    assert abs(_area(out) - _area(piece)) <= tol * _perimeter(piece)
    # and the whole way to a STEP file
    shapes, _, r = _export(s["features"], s["outlines"], s["frame"], boundary="outline")
    face = _one(r["products"]["Table top"], "plane")
    assert face["loop_kinds"] == ["outer", "inner", "inner"]
    assert missing_references(r["file"]) == []


def test_an_outline_that_cannot_be_a_face_gives_the_rectangle():
    features, _, frame = _box()
    top = _by_id(features, "f-top")
    # an outline that crosses itself where it was measured (a bow tie), in the top face's basis
    bow = [[-30.0, -20.0], [30.0, 20.0], [30.0, -20.0], [-30.0, 20.0]]
    outline = {"kind": "plane", "origin": top["extent"]["origin"], "u": top["extent"]["u"], "v": top["extent"]["v"],
               "outer": [bow], "holes": []}
    shapes, _, r = _export(features, {"f-top": outline}, frame, boundary="outline")
    face = _one(r["products"][top["name"]], "plane")
    assert len(face["loops"]) == 1 and len(face["loops"][0]) == 4  # the rectangle
    assert any("Top face: its outer loop crosses or touches itself" in n for n in shapes.notes)
    assert any("Top face: no valid face could be made from its outline: written with its rectangle" in n
               for n in shapes.notes)


def test_a_hole_touching_the_outline_is_left_out():
    a = np.linspace(0, 2 * np.pi, 400, endpoint=False)
    outer = np.c_[50 + 50 * np.cos(a), 50 + 50 * np.sin(a)]
    inside = np.c_[50 + 10 * np.cos(a), 50 + 10 * np.sin(a)][::-1]
    touching = np.c_[90.0005 + 10 * np.cos(a), 50 + 10 * np.sin(a)][::-1]  # reaches the outer loop
    out, holes, _, notes = cs.simplify_outline([outer], [inside, touching])
    assert len(holes) == 1 and cs.face_problems(out, holes) == []
    assert np.allclose(holes[0].mean(axis=0), [50, 50], atol=0.5)
    assert notes == ["1 hole was left out because it crosses or touches itself, the outer loop or another hole"]


def test_same_names_get_distinct_products():
    features, outlines, frame = _box()
    for f in features:
        if f["id"] in ("f-top", "f-side"):
            f["name"] = "Face"
        if f["id"] == "f-hole":
            f["name"] = "x axis"  # like a part axis, in other letters
    _, _, r = _export(features, outlines, frame)
    names = set(r["products"])
    assert {"Face", "Face (2)", "x axis (2)", "x axis (2) axis", "X axis", "Y axis", "Z axis"} <= names
    _close(_one(r["products"]["x axis (2)"], "circle")["center"], [0, 0, 0])  # the hole kept its geometry
    assert _one(r["products"]["X axis"], "line")["end"][0] > 0  # the part's X axis kept its name


def test_plane_with_its_outline_and_holes():
    s = step_samples.sample_set()["plate_outline.step"]
    shapes, _, r = _export(s["features"], s["outlines"], s["frame"], boundary="outline")
    plate = s["features"][0]
    face = _one(r["products"][plate["name"]], "plane")
    assert face["loop_kinds"][0] == "outer" and face["loop_kinds"].count("outer") == 1
    assert face["loop_kinds"].count("inner") == 2  # the two real holes; the pits are left out
    assert sum(len(x) for x in face["loops"]) <= cs.OUTLINE_MAX_POINTS
    n = np.asarray(plate["shape"]["normal"])
    for loop in face["loops"]:
        _close((loop - np.asarray(plate["shape"]["point"])) @ n, np.zeros(len(loop)))
    # the outline's area within the tolerance times its perimeter
    x, y = face["ref"], np.cross(face["normal"], face["ref"])
    areas = [_area(np.c_[(lp - face["point"]) @ x, (lp - face["point"]) @ y]) for lp in face["loops"]]
    assert areas[0] > 0 and all(a < 0 for a in areas[1:])
    ol = s["outlines"][plate["id"]]
    full = _area(np.asarray(ol["outer"][0])) + sum(_area(np.asarray(h)) for h in ol["holes"][:2])
    perim = _perimeter(np.asarray(ol["outer"][0])) + sum(_perimeter(np.asarray(h)) for h in ol["holes"][:2])
    assert abs(sum(areas) - full) <= cs.OUTLINE_TOLERANCE_MM * perim


def test_outline_asked_for_but_missing_falls_back_to_the_rectangle():
    features, _, frame = _box()
    shapes, _, r = _export(features, {}, frame, boundary="outline")
    top = _by_id(features, "f-top")
    assert len(_one(r["products"][top["name"]], "plane")["loops"][0]) == 4
    assert any(top["name"] in n and "rectangle" in n for n in shapes.notes)


# --------------------------------------------------------------------------- cylinders
def _span_and_arc(face):
    """A cylindrical face's span along its axis and its arc (degrees), from its boundary edges."""
    a = face["axis"] / np.linalg.norm(face["axis"])
    x = face["ref"]
    y = np.cross(a, x)
    circles = [e for e in face["edges"] if e["kind"] == "circle"]
    along = sorted(float((e["center"] - face["point"]) @ a) for e in circles)
    q = circles[0]["start"] - circles[0]["center"]
    start = math.degrees(math.atan2(q @ y, q @ x)) % 360
    closed = np.allclose(circles[0]["start"], circles[0]["end"], atol=TOL)
    if closed:
        arc = 360.0
    else:
        q1 = circles[0]["end"] - circles[0]["center"]
        end = math.degrees(math.atan2(q1 @ y, q1 @ x)) % 360
        arc = (end - start) % 360 if circles[0]["forward"] else (start - end) % 360
        if not circles[0]["forward"]:
            start = end
    return along, start, arc, circles


@pytest.mark.parametrize("sample, arc_deg", [("full_cylinder.step", 360.0), ("part_cylinder.step", 90.0)])
def test_cylinder_span_and_arc(sample, arc_deg):
    s = step_samples.sample_set()[sample]
    f = s["features"][0]
    _, _, r = _export(s["features"], s["outlines"], s["frame"])
    face = _one(r["products"][f["name"]], "cylinder")
    _close(face["radius"], f["shape"]["radius_mm"])
    _parallel(face["axis"], f["shape"]["axis"])
    along, start, arc, circles = _span_and_arc(face)
    _close(along, f["extent"]["along_mm"])
    assert len(circles) == 2
    for c in circles:
        _close(c["radius"], f["shape"]["radius_mm"])
        _close(np.linalg.norm(c["start"] - c["center"]), f["shape"]["radius_mm"])
    assert abs(arc - arc_deg) < 1e-6
    if arc_deg < 360:
        assert abs((start - f["extent"]["start_deg"] + 180) % 360 - 180) < 1e-6
        assert len(face["edges"]) == 4
    else:
        assert len(face["edges"]) == 4  # two circles and the seam, used twice
        lines = [e for e in face["edges"] if e["kind"] == "line"]
        assert len(lines) == 2 and lines[0]["forward"] != lines[1]["forward"]


# --------------------------------------------------------------------------- features without an extent
def test_feature_without_an_extent_gets_the_default_size_and_a_note():
    features, outlines, frame = _box()
    for f in features:
        f["extent"] = None
    shapes, _, r = _export(features, None, frame)
    top = _by_id(features, "f-top")
    face = _one(r["products"][top["name"]], "plane")
    loop = face["loops"][0]
    sides = np.linalg.norm(np.diff(np.vstack([loop, loop[:1]]), axis=0), axis=1)
    _close(sides, [cs.DEFAULT_PLANE_SIDE_MM] * 4)
    want = PartFrame.from_dict(frame).fit_to_part(top["shape"])
    _close(loop.mean(axis=0), want["point"])  # centred on the fitted point
    boss = _by_id(features, "f-boss")
    along, _, arc, _ = _span_and_arc(_one(r["products"][boss["name"]], "cylinder"))
    _close(along, [-cs.DEFAULT_CYLINDER_LENGTH_MM / 2, cs.DEFAULT_CYLINDER_LENGTH_MM / 2])
    assert arc == 360.0
    for f in (top, boss):
        assert any(f["name"] in n and "no measured size" in n for n in shapes.notes)
    hole = _by_id(features, "f-hole")
    assert not any(hole["name"] in n for n in shapes.notes)  # a hole's circle needs no extent
    assert all(n in r["description"] for n in shapes.notes)


# --------------------------------------------------------------------------- FreeCAD
def _freecad() -> str | None:
    given = os.environ.get("OHS_FREECAD")
    if given:
        return given if Path(given).is_file() else None
    places = [r"C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\FreeCAD 1.1\bin\FreeCADCmd.exe")]
    places += sorted(glob.glob(os.path.expanduser(r"~\Tools\FreeCAD-*\*\bin\freecadcmd.exe")))
    return next((p for p in places if Path(p).is_file()), None)


def _freecad_report(exe: str, path: Path) -> dict:
    out = path.with_suffix(".json")
    env = dict(os.environ, OHS_STEP_IN=str(path), OHS_CHECK_OUT=str(out))
    proc = subprocess.run([exe, str(TOOLS / "freecad_check.py")], env=env, capture_output=True, text=True,
                          timeout=120)
    assert out.is_file(), f"FreeCAD wrote no report for {path.name}:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(out.read_text(encoding="utf-8"))


def _need_freecad() -> str:
    exe = _freecad()
    if exe is None:
        pytest.skip("FreeCAD is not installed (set OHS_FREECAD to its FreeCADCmd.exe)")
    return exe


def test_round_trip_through_freecad(tmp_path):
    exe = _need_freecad()
    samples = step_samples.sample_set()
    for name in ("box_with_hole_and_boss.step", "full_cylinder.step", "part_cylinder.step", "plate_outline.step"):
        _check_in_freecad(exe, tmp_path / name, samples[name])


def test_ragged_table_through_freecad(tmp_path):
    """The ragged table's simplified outline (about 2,000 edges and two holes): FreeCAD says it is
    valid. FreeCAD takes about 7 s to read it, the rest of its samples 0.5 s each."""
    exe = _need_freecad()
    _check_in_freecad(exe, tmp_path / "ragged_table.step", step_samples.sample_set()["ragged_table.step"])


def _check_in_freecad(exe: str, path: Path, s: dict) -> None:
    """Write one sample, read it in FreeCAD, and compare what it shows with the shapes to 1e-6 mm."""
    shapes = cs.cad_shapes(s["features"], outlines=s["outlines"], frame=s["frame"], boundary=s["boundary"])
    step.write_step(path, shapes, title=s["title"])
    report = _freecad_report(exe, path)
    assert report["error"] is None, report["error"]
    objs = {o["name"]: o for o in report["shapes"]}
    for sh in shapes.shapes:
        assert sh.name in objs, f"{path.name}: FreeCAD shows no {sh.name!r}; it shows {sorted(objs)}"
        o = objs[sh.name]
        assert o["valid"], f"{path.name}: {sh.name!r} is not valid in FreeCAD"
        if isinstance(sh, cs.PlaneFace):
            (fc,) = o["faces"]
            assert fc["surface"] == "Plane"
            assert len(o["edges"]) == len(sh.outer) + sum(len(h) for h in sh.holes)
            _parallel(fc["normal"], sh.normal)
            assert abs((np.asarray(fc["position"]) - sh.point) @ sh.normal) < TOL
        elif isinstance(sh, cs.CylinderFace):
            (fc,) = o["faces"]
            assert fc["surface"] == "Cylinder"
            _parallel(fc["axis"], sh.axis)
            assert abs(fc["radius"] - sh.radius) < TOL
            assert np.linalg.norm(np.cross(np.asarray(fc["center"]) - sh.point, sh.axis)) < TOL
            umin, umax, vmin, vmax = fc["bounds"]  # the arc (radians) and the span (mm)
            assert abs((umax - umin) - math.radians(sh.arc_deg)) < 1e-9
            assert abs((vmax - vmin) - (sh.along[1] - sh.along[0])) < TOL
            ends = [sh.point + t * sh.axis for t in sh.along]
            circles = sorted((e for e in o["edges"] if e["curve"] == "Circle"),
                             key=lambda e: float((np.asarray(e["center"]) - sh.point) @ sh.axis))
            assert len(circles) == 2
            for e, end in zip(circles, ends):
                _close(e["center"], end)
                _close(e["radius"], sh.radius)
            (axis_edge,) = objs[f"{sh.name} axis"]["edges"]
            assert axis_edge["curve"] == "Line"
            _close(axis_edge["start"], ends[0])
            _close(axis_edge["end"], ends[1])
        elif isinstance(sh, cs.HoleCircle):
            (circle,) = o["edges"]
            assert circle["curve"] == "Circle"
            _close(circle["center"], sh.center)
            _close(circle["radius"], sh.radius)
            _parallel(circle["normal"], sh.normal)
            (line,) = objs[f"{sh.name} axis"]["edges"]
            assert line["curve"] == "Line" and objs[f"{sh.name} axis"]["valid"]
            _close(0.5 * (np.asarray(line["start"]) + line["end"]), sh.center)
            _parallel(np.asarray(line["end"]) - line["start"], sh.normal)
    if shapes.axes is not None:
        # FreeCAD's STEP import shows no lone points (the origin is where the axes meet)
        for name, d in (("X axis", [1, 0, 0]), ("Y axis", [0, 1, 0]), ("Z axis", [0, 0, 1])):
            assert objs[name]["valid"], name
            (e,) = objs[name]["edges"]
            assert e["curve"] == "Line"
            _close(e["start"], [0, 0, 0])
            _close(e["end"], shapes.axes.length_mm * np.asarray(d, float))
