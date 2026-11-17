"""``ohs.cad.dxf`` and ``ohs.cad.svg`` (FEATURE_PLAN I1): a section written as DXF and SVG at 1:1.

The DXF is read back with a small reader of the tests' own (``tests/dxf_reader.py``): the same
points to 1e-6 mm, measured and not-measured stretches on their two layers, closed loops closed,
millimetres declared. The SVG is parsed as XML: its size in mm agrees with its viewBox, the
drawing is not mirrored, the two kinds of stretch look different and the title is escaped. Both
are byte-identical for the same input. The last test reads the DXF in FreeCAD's command-line
program (``tools/freecad_dxf_check.py``); it is skipped when FreeCAD is not installed.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dxf_reader import read as read_dxf  # noqa: E402
from test_mesh_section import plate_with_hole, voxels  # noqa: E402

from ohs.cad import dxf, svg  # noqa: E402
from ohs.mesh import section as sec  # noqa: E402

TOL = 1e-6  # mm
NS = {"svg": "http://www.w3.org/2000/svg"}
TOOLS = Path(__file__).resolve().parents[1] / "tools"


def plate_section(title: str | None = None) -> sec.Section:
    """The plate with a hole cut flat: an outline and a hole, the right half filled (not measured)."""
    V, F = plate_with_hole()
    filled = V[F].mean(axis=1)[:, 0] > 5.0
    plane = sec.plane_from_point_normal([0, 0, 1.37], [0, 0, 1], description=title)
    return sec.section(V, F, plane, filled=filled)


def l_section() -> sec.Section:
    """An L-shaped bar cut across: the outline (0,0) (4,0) (4,1) (1,1) (1,3) (0,3), all measured."""
    m = np.zeros((4, 3, 2), bool)
    m[:, 0, :] = True
    m[0, :, :] = True
    V, F = voxels(m)
    return sec.section(V, F, sec.plane_from_point_normal([0, 0, 0.5], [0, 0, 1]))


def expected_pieces(s: sec.Section) -> list[tuple[str, bool, np.ndarray]]:
    """What the files should hold: one piece per stretch, its layer, closed or not, its points."""
    out = []
    for ln in s.lines:
        for P, filled, closed in ln.stretches():
            out.append((dxf.LAYER_NOT_MEASURED if filled else dxf.LAYER_MEASURED, closed, P))
    return out


# --------------------------------------------------------------------------- DXF
def test_dxf_reads_back():
    s = plate_section()
    text = dxf.dxf_text(s)
    assert text.isascii()
    d = read_dxf(text)
    assert d["header"]["$ACADVER"] == "AC1009"
    assert d["header"]["$INSUNITS"] == "4" and d["header"]["$MEASUREMENT"] == "1"  # millimetres, metric
    assert set(d["layers"]) >= {dxf.LAYER_MEASURED, dxf.LAYER_NOT_MEASURED}
    assert d["layers"][dxf.LAYER_MEASURED]["linetype"] != d["layers"][dxf.LAYER_NOT_MEASURED]["linetype"]
    for lay in d["layers"].values():
        assert lay["linetype"] in d["linetypes"]
    want = expected_pieces(s)
    assert len(d["polylines"]) == len(want)
    for got, (layer, closed, P) in zip(d["polylines"], want):
        assert got["layer"] == layer and got["closed"] == closed
        assert got["points"].shape == P.shape and np.abs(got["points"] - P).max() <= TOL
    assert {p["layer"] for p in d["polylines"]} == {dxf.LAYER_MEASURED, dxf.LAYER_NOT_MEASURED}


def test_dxf_closed_loops_and_open_lines():
    d = read_dxf(dxf.dxf_text(l_section()))
    assert len(d["polylines"]) == 1
    pl = d["polylines"][0]
    assert pl["closed"] and pl["layer"] == dxf.LAYER_MEASURED and len(pl["points"]) >= 6
    # an open sheet: an open line
    V = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [10, 10, 0.0]])
    F = np.array([[0, 1, 3], [0, 3, 2]])
    s = sec.section(V, F, sec.plane_from_point_normal([3.25, 0, 0], [1, 0, 0]))
    d = read_dxf(dxf.dxf_text(s))
    assert len(d["polylines"]) == 1 and not d["polylines"][0]["closed"]
    assert np.allclose(d["polylines"][0]["points"], s.lines[0].points, atol=TOL)


def test_dxf_is_deterministic(tmp_path):
    a = dxf.dxf_text(plate_section())
    assert a == dxf.dxf_text(plate_section())
    p1 = dxf.write_dxf(tmp_path / "a.dxf", plate_section())
    p2 = dxf.write_dxf(tmp_path / "b.dxf", plate_section())
    assert p1.read_bytes() == p2.read_bytes() == a.encode("ascii")
    # an empty section is still a file a CAD program opens
    V, F = plate_with_hole()
    empty = sec.section(V, F, sec.plane_from_point_normal([0, 0, 50], [0, 0, 1]))
    assert read_dxf(dxf.dxf_text(empty))["polylines"] == []


# --------------------------------------------------------------------------- SVG
def _mm(text: str) -> float:
    m = re.fullmatch(r"([0-9.]+)mm", text)
    assert m, text
    return float(m.group(1))


def _path_points(d: str) -> tuple[np.ndarray, bool]:
    tokens = d.replace(",", " ").split()
    pts, closed, i = [], False, 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("M", "L"):
            pts.append((float(tokens[i + 1]), float(tokens[i + 2])))
            i += 3
        elif t == "Z":
            closed = True
            i += 1
        else:
            raise ValueError(f"unexpected path token {t!r}")
    return np.asarray(pts), closed


def _paths(root) -> list:
    return [p for p in root.iter("{http://www.w3.org/2000/svg}path") if p.get("class") in ("measured", "not-measured")]


def test_svg_is_one_to_one_and_not_mirrored():
    s = l_section()
    root = ET.fromstring(svg.svg_text(s))
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    W, H = _mm(root.get("width")), _mm(root.get("height"))
    vb = [float(x) for x in root.get("viewBox").split()]
    assert vb == [0.0, 0.0, W, H]  # one user unit is one millimetre
    u0, v0, u1, v1 = s.bbox
    assert W >= u1 - u0 and H >= v1 - v0
    paths = _paths(root)
    assert len(paths) == 1
    P, closed = _path_points(paths[0].get("d"))
    assert closed
    Q = s.lines[0].points
    assert P.shape == Q.shape
    # x = u + a, y = -v + b: the same shift for every point, the y axis turned so up is up
    a, b = P[:, 0] - Q[:, 0], P[:, 1] + Q[:, 1]
    assert np.ptp(a) < 1e-3 and np.ptp(b) < 1e-3
    # the L's long leg (along u) is at the bottom of the picture, its short leg up the left
    top = P[np.argmin(P[:, 1])]
    assert top[0] - P[:, 0].min() < 1.0 + 1e-3
    assert np.all(P >= 0) and np.all(P[:, 0] <= W) and np.all(P[:, 1] <= H)


def test_svg_styles_scale_bar_and_title():
    title = 'Cut <A> & "B" \'C\''
    s = plate_section(title)
    text = svg.svg_text(s)
    root = ET.fromstring(text)
    assert root.find("svg:title", NS).text == title
    assert any(t.text == title for t in root.iter("{http://www.w3.org/2000/svg}text"))
    assert "<A>" not in text and '"B"' not in text.split("<title>")[1].split("</title>")[0]
    paths = _paths(root)
    want = expected_pieces(s)
    assert len(paths) == len(want)
    for p, (layer, closed, Q) in zip(paths, want):
        assert p.get("class") == ("not-measured" if layer == dxf.LAYER_NOT_MEASURED else "measured")
        P, c = _path_points(p.get("d"))
        assert c == closed and P.shape == Q.shape
    style = {c: (p.get("stroke"), p.get("stroke-dasharray")) for p in paths for c in [p.get("class")]}
    assert style["measured"] != style["not-measured"] and style["not-measured"][1]
    bar = root.find(".//svg:g[@id='scale-bar']", NS)
    assert bar is not None
    line = bar.find("svg:line", NS)
    label = bar.find("svg:text", NS).text
    n = float(re.fullmatch(r"([0-9.]+) mm", label).group(1))
    assert abs(float(line.get("x2")) - float(line.get("x1")) - n) < 1e-9


def test_svg_is_deterministic(tmp_path):
    a = svg.svg_text(plate_section("t"))
    assert a == svg.svg_text(plate_section("t"))
    p1 = svg.write_svg(tmp_path / "a.svg", plate_section("t"))
    p2 = svg.write_svg(tmp_path / "b.svg", plate_section("t"))
    assert p1.read_bytes() == p2.read_bytes() == a.encode("utf-8")
    V, F = plate_with_hole()
    empty = sec.section(V, F, sec.plane_from_point_normal([0, 0, 50], [0, 0, 1]))
    root = ET.fromstring(svg.svg_text(empty))
    assert _paths(root) == [] and _mm(root.get("width")) > 0


# --------------------------------------------------------------------------- FreeCAD
def _freecad() -> str | None:
    given = os.environ.get("OHS_FREECAD")
    if given:
        return given if Path(given).is_file() else None
    places = [r"C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\FreeCAD 1.1\bin\FreeCADCmd.exe")]
    places += sorted(glob.glob(os.path.expanduser(r"~\Tools\FreeCAD-*\*\bin\freecadcmd.exe")))
    return next((p for p in places if Path(p).is_file()), None)


def _segments_of(pieces) -> np.ndarray:
    """Every segment of the pieces as ``(x0, y0, x1, y1)``, its ends in order, sorted."""
    out = []
    for P in pieces:
        out += [sorted([tuple(a), tuple(b)]) for a, b in zip(P[:-1], P[1:])]
    A = np.asarray(out, float).reshape(-1, 4)
    return A[np.lexsort(np.round(A, 4).T[::-1])]


def _in_freecad(exe: str, path: Path) -> dict:
    out = path.with_suffix(".json")
    env = dict(os.environ, OHS_DXF_IN=str(path), OHS_CHECK_OUT=str(out))
    proc = subprocess.run([exe, str(TOOLS / "freecad_dxf_check.py")], env=env, capture_output=True, text=True,
                          timeout=120)
    assert out.is_file(), f"FreeCAD wrote no report:\n{proc.stdout}\n{proc.stderr}"
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["error"] is None, report["error"]
    return report


def test_dxf_through_freecad(tmp_path):
    """FreeCAD's own DXF importer (the built-in one, as File > Import uses by default, its settings
    untouched) reads the file in mm at scale 1: every segment of every stretch comes back as a
    straight edge with the same ends to 1e-6 mm, on its layer, and nothing else; a closed loop is a
    closed wire."""
    exe = _freecad()
    if exe is None:
        pytest.skip("FreeCAD is not installed (set OHS_FREECAD to its FreeCADCmd.exe)")
    s = plate_section()
    report = _in_freecad(exe, dxf.write_dxf(tmp_path / "plate.dxf", s))
    st = report["stats"]
    assert st["dxfVersion"] == "AC1009" and st["fileUnits"] == "Millimeters" and st["finalScalingFactor"] == 1.0
    assert all(o["valid"] for o in report["objects"]) and len(report["objects"]) == len(expected_pieces(s))
    assert {e["curve"] for e in report["edges"]} == {"Line"}
    assert all(e["start"][2] == 0.0 and e["end"][2] == 0.0 for e in report["edges"])
    for layer in (dxf.LAYER_MEASURED, dxf.LAYER_NOT_MEASURED):
        got = [np.array([e["start"][:2], e["end"][:2]]) for e in report["edges"] if e["layer"] == layer]
        want = [np.vstack([P, P[:1]]) if closed else P for lay, closed, P in expected_pieces(s) if lay == layer]
        g, w = _segments_of(got), _segments_of(want)
        assert len(g) and g.shape == w.shape, (layer, g.shape, w.shape)
        assert np.abs(g - w).max() <= TOL
    # a closed loop on one layer: one closed wire
    s = l_section()
    report = _in_freecad(exe, dxf.write_dxf(tmp_path / "l.dxf", s))
    assert len(report["objects"]) == 1 and report["objects"][0]["closed"]
    assert report["objects"][0]["layer"] == dxf.LAYER_MEASURED
    assert report["objects"][0]["edges"] == len(s.lines[0].points)
