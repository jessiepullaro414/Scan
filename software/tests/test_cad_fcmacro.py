"""``ohs.cad.fcmacro`` (FEATURE_PLAN C2): the kept fits as a FreeCAD macro.

The macro for every sample of ``tools/step_samples.py`` is valid Python, the same shapes give the
same bytes, every fit's name is an object's label, and a name made to look like code stays text:
the compiled macro's string constants hold it exactly, and nothing of it is left once the string
literals and comments are taken out. The last tests run the macros in FreeCAD's command-line
program (``tools/freecad_macro_check.py``) and compare every object FreeCAD built with what
FreeCAD reads from the same shapes' STEP file, to 1e-6 mm; they are skipped when FreeCAD is not
installed (found as ``tests/test_cad_step.py`` finds it).
"""
from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import tokenize
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_cad_step import _freecad, _freecad_report  # noqa: E402  (FreeCAD found exactly as the STEP tests find it)
from tools import step_samples  # noqa: E402

from ohs.cad import fcmacro  # noqa: E402
from ohs.cad import shapes as cs  # noqa: E402
from ohs.cad import step  # noqa: E402

TOL = 1e-6  # mm, and for unit directions
TOOLS = Path(__file__).resolve().parents[1] / "tools"
BUILDERS = {"plane", "cylinder", "hole", "axis", "point", "part_axis", "part_origin"}
HOSTILE = ("Bob's \"top\" face \\ ''' \"\"\" \n__import__('os').system('echo pwned') # \r\n"
           "\\x41 {0} %s   Ø 14 日本 \U0001f600\t\x00 end")


# --------------------------------------------------------------------------- helpers
def _shapes(s: dict) -> cs.CadShapes:
    return cs.cad_shapes(s["features"], outlines=s["outlines"], frame=s["frame"], boundary=s["boundary"])


def _labels(text: str) -> list[tuple[str, str]]:
    """``(builder, label)`` of every builder call in the macro, in order (the label is the first argument)."""
    out = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BUILDERS:
            first = node.args[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str), ast.dump(first)
            out.append((node.lineno, node.func.id, first.value))
    return [(b, label) for _, b, label in sorted(out)]


def _expected_labels(shapes: cs.CadShapes) -> list[tuple[str, str]]:
    """Each object the macro should make, as the STEP writer names them (``tests/test_cad_step.py``)."""
    out = []
    for s in shapes.shapes:
        if isinstance(s, cs.PlaneFace):
            out.append(("plane", s.name))
        elif isinstance(s, cs.CylinderFace):
            out += [("cylinder", s.name), ("axis", f"{s.name} axis")]
        elif isinstance(s, cs.HoleCircle):
            out += [("hole", s.name), ("axis", f"{s.name} axis")]
        elif isinstance(s, cs.PointMark):
            out.append(("point", s.name))
    if shapes.axes is not None:
        out += [("part_axis", n) for n in shapes.axes.names] + [("part_origin", shapes.axes.origin_name)]
    return out


def _code_without_text(text: str) -> str:
    """The macro with its string literals and comments taken out: what Python runs as code."""
    toks = tokenize.generate_tokens(io.StringIO(text).readline)
    return " ".join(t.string for t in toks if t.type not in (tokenize.STRING, tokenize.COMMENT))


def _strings(text: str) -> set[str]:
    return {n.value for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


# --------------------------------------------------------------------------- the text
@pytest.mark.parametrize("sample", sorted(step_samples.sample_set()))
def test_every_sample_is_valid_python_with_every_name_as_a_label(sample):
    s = step_samples.sample_set()[sample]
    shapes = _shapes(s)
    text = fcmacro.macro_text(shapes, title=s["title"])
    compile(text, sample, "exec")
    assert text.isascii() and "\r" not in text and text.endswith("\n")
    assert _labels(text) == _expected_labels(shapes)
    assert s["title"] in _strings(text)  # the document is named after the part
    for note in shapes.notes:  # the notes as comments at the top, before any code
        assert fcmacro.comment_text(note) in text.split("\nimport ")[0]


def test_same_shapes_give_the_same_bytes(tmp_path):
    a = fcmacro.macro_text(_shapes(step_samples.sample_set()["box_with_hole_and_boss.step"]), title="Box")
    b = fcmacro.macro_text(_shapes(step_samples.sample_set()["box_with_hole_and_boss.step"]), title="Box")
    assert a == b
    p1 = fcmacro.write_macro(tmp_path / "a.FCMacro", _shapes(step_samples.sample_set()["box_with_hole_and_boss.step"]),
                             title="Box")
    p2 = fcmacro.write_macro(tmp_path / "b.FCMacro", _shapes(step_samples.sample_set()["box_with_hole_and_boss.step"]),
                             title="Box")
    assert p1.read_bytes() == p2.read_bytes() == a.encode("ascii")


def test_the_frame_is_said_at_the_top():
    box = step_samples.sample_set()["box_with_hole_and_boss.step"]
    head = fcmacro.macro_text(_shapes(box), title="Box").split("\nimport ")[0]
    assert "in the part's axes" in head
    cyl = step_samples.sample_set()["full_cylinder.step"]
    head = fcmacro.macro_text(_shapes(cyl), title="Tube").split("\nimport ")[0]
    assert "in the scan's own axes" in head


def test_a_name_that_looks_like_code_stays_text():
    s = step_samples.sample_set()["box_with_hole_and_boss.step"]
    for f in s["features"]:
        if f["id"] == "f-top":
            f["name"] = HOSTILE
            f["extent"] = None  # so a note carries the name too (a comment at the top)
    shapes = _shapes(s)
    assert any(HOSTILE in n for n in shapes.notes)
    text = fcmacro.macro_text(shapes, title="Part " + HOSTILE)
    compile(text, "hostile", "exec")
    assert text.isascii()
    strings = _strings(text)
    assert HOSTILE in strings and "Part " + HOSTILE in strings and f"{HOSTILE} axis" not in strings
    assert ("plane", HOSTILE) in _labels(text)
    code = _code_without_text(text)
    for piece in ("__import__", "pwned", "system", "echo", "Bob", " ", "\x00"):
        assert piece not in code, piece
    # every comment is one line of printable ASCII: nothing of a note can run on as code
    for line in text.splitlines():
        assert all(0x20 <= ord(c) < 0x7F for c in line), line
    # the same name twice (and one like a group's) gets the STEP writer's " (2)"
    s = step_samples.sample_set()["box_with_hole_and_boss.step"]
    for f in s["features"]:
        if f["id"] in ("f-top", "f-side"):
            f["name"] = "Face"
    labels = [x for _, x in _labels(fcmacro.macro_text(_shapes(s), title="Box"))]
    assert labels[:2] == ["Face", "Face (2)"] and len(set(labels)) == len(labels)


# --------------------------------------------------------------------------- FreeCAD
def _need_freecad() -> str:
    exe = _freecad()
    if exe is None:
        pytest.skip("FreeCAD is not installed (set OHS_FREECAD to its FreeCADCmd.exe)")
    return exe


def _macro_reports(exe: str, macros: list[Path]) -> list[dict]:
    """Run each macro in FreeCAD's command-line program (one run for all) and read what it built."""
    outs = [m.with_suffix(".macro.json") for m in macros]
    env = dict(os.environ, OHS_MACRO_IN=os.pathsep.join(map(str, macros)), OHS_CHECK_OUT=os.pathsep.join(map(str, outs)))
    proc = subprocess.run([exe, str(TOOLS / "freecad_macro_check.py")], env=env, capture_output=True, text=True,
                          timeout=300)
    reports = []
    for m, out in zip(macros, outs):
        assert out.is_file(), f"FreeCAD wrote no report for {m.name}:\n{proc.stdout}\n{proc.stderr}"
        reports.append(json.loads(out.read_text(encoding="utf-8")))
    return reports


class _Diff:
    """The largest difference seen between the macro's objects and the STEP file's."""

    def __init__(self):
        self.mm = 0.0

    def close(self, a, b, what: str):
        d = float(np.max(np.abs(np.asarray(a, float) - np.asarray(b, float)))) if np.size(a) else 0.0
        self.mm = max(self.mm, d)
        assert d <= TOL, (what, a, b)


def _edge_key(e: dict) -> np.ndarray:
    """An edge's two ends, the smaller first, and its centre (a circle's; else its middle), as 9 numbers."""
    a, b = np.asarray(e["start"], float), np.asarray(e["end"], float)
    if tuple(b) < tuple(a):
        a, b = b, a
    c = np.asarray(e.get("center", (a + b) / 2), float)
    return np.r_[a, b, c]


def _same_edges(mine: list, theirs: list, diff: _Diff, what: str):
    assert len(mine) == len(theirs), (what, len(mine), len(theirs))
    for curve in {e["curve"] for e in mine} | {e["curve"] for e in theirs}:
        A = [e for e in mine if e["curve"] == curve]
        B = [e for e in theirs if e["curve"] == curve]
        assert len(A) == len(B), (what, curve, len(A), len(B))
        KA, KB = np.array([_edge_key(e) for e in A]), np.array([_edge_key(e) for e in B])
        for i, k in enumerate(KA):  # each edge of the macro's has its twin in the STEP file's
            d = np.abs(KB - k).max(axis=1)
            j = int(np.argmin(d))
            diff.close(k, KB[j], f"{what}: a {curve} edge")
            diff.close(A[i]["length"], B[j]["length"], f"{what}: a {curve} edge's length")
            for key in ("radius", "normal"):
                if key in A[i]:
                    if key == "normal":
                        assert abs(abs(float(np.dot(A[i][key], B[j][key]))) - 1.0) <= TOL, (what, key)
                    else:
                        diff.close(A[i][key], B[j][key], f"{what}: a {curve}'s {key}")


def _compare(macro: dict, step_report: dict, shapes: cs.CadShapes, title: str, name: str) -> float:
    """Every object the macro built against the STEP file's object of the same name; returns the
    largest difference in mm."""
    assert macro["error"] is None, macro["error"]
    assert step_report["error"] is None, step_report["error"]
    assert macro["document"] == title
    built = {o["name"]: o for o in macro["shapes"]}
    read = {o["name"]: o for o in step_report["shapes"]}
    want = _expected_labels(shapes)
    assert [o["name"] for o in macro["shapes"]] == [n for _, n in want], name
    group_of = {"plane": "Planes", "cylinder": "Cylinders", "hole": "Holes", "axis": "Axes", "point": "Points",
                "part_axis": "Part frame", "part_origin": "Part frame"}
    diff = _Diff()
    for builder, label in want:
        o = built[label]
        assert o["valid"], f"{name}: {label!r} is not valid in FreeCAD"
        assert o["group"] == group_of[builder], (label, o["group"])
        if builder == "part_origin":
            diff.close(o["vertices"], [[0.0, 0.0, 0.0]], f"{label}: the origin")
            continue
        if builder == "point":
            sh = next(s for s in shapes.shapes if isinstance(s, cs.PointMark) and s.name == label)
            diff.close(o["vertices"], [sh.point], f"{label}: the point")
            continue
        r = read[label]  # FreeCAD's STEP import shows every face and edge under the same name
        assert r["valid"]
        assert len(o["faces"]) == len(r["faces"]), label
        for fa, fb in zip(o["faces"], r["faces"]):
            assert fa["surface"] == fb["surface"], (label, fa["surface"], fb["surface"])
            assert fa["surface"] == {"plane": "Plane", "cylinder": "Cylinder"}[builder]
            diff.close(fa["bounds"], fb["bounds"], f"{label}: the parameter range")
            assert abs(fa["area"] - fb["area"]) <= 1e-9 * max(1.0, fb["area"]), (label, fa["area"], fb["area"])
            keys = ("position", "normal") if fa["surface"] == "Plane" else ("center", "axis", "radius")
            for key in keys:
                diff.close(fa[key], fb[key], f"{label}: {key}")
        _same_edges(o["edges"], r["edges"], diff, label)
        diff.close(o["bound_box"], r["bound_box"], f"{label}: the bounding box")
    return diff.mm


def _check_samples(exe: str, tmp_path: Path, names: list[str]) -> dict:
    samples = step_samples.sample_set()
    macros, made = [], []
    for name in names:
        s = samples[name]
        shapes = _shapes(s)
        macros.append(fcmacro.write_macro(tmp_path / (Path(name).stem + ".FCMacro"), shapes, title=s["title"]))
        made.append((name, s, shapes))
    worst = {}
    for (name, s, shapes), report in zip(made, _macro_reports(exe, macros)):
        path = step.write_step(tmp_path / name, shapes, title=s["title"])
        worst[name] = _compare(report, _freecad_report(exe, path), shapes, s["title"], name)
    return worst


def test_macros_build_the_step_files_shapes_in_freecad(tmp_path):
    exe = _need_freecad()
    worst = _check_samples(exe, tmp_path, ["box_with_hole_and_boss.step", "full_cylinder.step", "part_cylinder.step",
                                           "plate_outline.step"])
    print("largest difference from the STEP round trip (mm):", worst)


def test_ragged_table_macro_in_freecad(tmp_path):
    """The ragged table's outline (about 2,000 edges and two holes): about 9.5 s in all, most of it
    FreeCAD reading the STEP file (7 s), as in ``test_cad_step.py``; not marked slow."""
    exe = _need_freecad()
    worst = _check_samples(exe, tmp_path, ["ragged_table.step"])
    print("largest difference from the STEP round trip (mm):", worst)


def test_a_hostile_name_is_a_label_in_freecad(tmp_path):
    exe = _need_freecad()
    s = step_samples.sample_set()["box_with_hole_and_boss.step"]
    name = HOSTILE.replace("\x00", "")  # FreeCAD's labels are C strings: a NUL ends them
    for f in s["features"]:
        if f["id"] == "f-top":
            f["name"] = name
    shapes = _shapes(s)
    path = fcmacro.write_macro(tmp_path / "hostile.FCMacro", shapes, title="Part " + name)
    (report,) = _macro_reports(exe, [path])
    assert report["error"] is None, report["error"]
    assert report["document"] == "Part " + name
    assert name in [o["name"] for o in report["shapes"]]
    assert not (tmp_path / "pwned").exists()
