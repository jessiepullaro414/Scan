"""ohs.report: the report of one scan as one self-contained HTML page (FEATURE_PLAN I3).

A full example is built here from small real records, each made by the module that makes it in the
app: fits of noisy points (``ohs.measure.fit``) kept as ``features.json`` records and checked by
``ohs.measure.features.validate``, a relation (``ohs.measure.relate``), the part's frame
(``ohs.measure.frame``), a section of a box (``ohs.mesh.section``), the Check answer of a grid
(``ohs.mesh.check``), a comparison of two sheets (``ohs.inspect.reference``) and the flush along a
seam between two made-up plates (``ohs.inspect.gapflush``). The page is then read back with the
standard library's ``html.parser``: what it holds, where, and that it reaches for nothing outside
itself. ``tests/test_report_tables.py`` uses the same example for the CSV and the JSON.
"""
from __future__ import annotations

import base64
import copy
import functools
import math
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from html.parser import HTMLParser

import numpy as np
import pytest

pytest.importorskip("open3d")  # the comparison's alignment engine

from mesh_shapes import box, grid  # noqa: E402

from ohs.measure import features as kf  # noqa: E402
from ohs.measure.fit import fit_circle, fit_cylinder, fit_plane  # noqa: E402
from ohs.measure.frame import part_frame  # noqa: E402
from ohs.measure.relate import relate  # noqa: E402
from ohs.mesh.check import weak_areas  # noqa: E402
from ohs.mesh.section import plane_from_point_normal, section  # noqa: E402

# a fit's and a section's names with markup and quotes: text, never markup
FIT_HOSTILE = '<script>alert("fit")</script> & \'Boss\''
SECTION_HOSTILE = 'Cut <b onmouseover="x()">A-A</b> "&"'
PICTURE_HOSTILE = '<img src=x onerror="y()"> view'
CREATED = "2026-11-17T10:00:00"


# --------------------------------------------------------------------------- the example's records
def _png(width: int = 2, height: int = 2) -> bytes:
    """A tiny real PNG (grey pixels), built by hand."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _feature(fid, name, kind, fit, *, extent=None, warnings=(), form=None, nominals=None, mesh_version=3) -> dict:
    shape = fit.to_dict()
    point = shape.get("point") or shape.get("center")
    out = {"id": fid, "name": name, "kind": kind, "shape": shape,
           "seed": {"kind": kind, "face": 0, "point": point, "tolerance_mm": 0.1},
           "n_faces": 500, "tolerance_mm": 0.1, "mean_sigma_mm": 0.02, "correlated_points": 4.0,
           "warnings": list(warnings), "mesh_version": mesh_version, "extent": extent, "created": CREATED}
    if kind == "cylinder":
        out["form"] = form
    if nominals is not None:
        out["nominals"] = nominals
    return out


def _plates(length: float = 30.0, gap: float = 4.0, flush: float = 1.0, width: float = 25.0, step: float = 0.5):
    """Two plates along x with a rounded (hemmed) edge each, ``gap`` apart, the second ``flush``
    higher (as ``tests/test_inspect_gapflush.py`` builds them, shorter). Returns V, F and the clicks."""
    r_edge = 1.0
    xs = np.arange(-length / 2, length / 2 + 1e-9, 1.0)
    Vs, Fs, n = [], [], 0
    for side, z in ((-1, 0.0), (1, flush)):
        yc, zc = side * (gap / 2 + r_edge), z - r_edge
        d = np.arange(width, 0.0, -step)
        top = np.column_stack([yc + side * d, np.full(len(d), z)])
        th = np.radians(np.arange(90.0, -60.0 - 1e-9, -7.5))
        arc = np.column_stack([yc - side * r_edge * np.cos(th), zc + r_edge * np.sin(th)])
        prof = np.vstack([top, arc])
        k = len(prof)
        V = np.vstack([np.column_stack([np.full(k, x), prof]) for x in xs])
        F = []
        for j in range(len(xs) - 1):
            a, b = j * k, (j + 1) * k
            for i in range(k - 1):
                F += [(a + i, b + i, a + i + 1), (a + i + 1, b + i, b + i + 1)]
        F = np.asarray(F, dtype=np.int64)
        nrm = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
        if np.median(nrm[:, 2]) < 0:
            F = F[:, ::-1].copy()
        Vs.append(V)
        Fs.append(F + n)
        n += len(V)
    a = np.array([0.0, -(gap / 2 + r_edge + 6.0), 0.0])
    b = np.array([0.0, gap / 2 + r_edge + 6.0, flush])
    return np.vstack(Vs), np.vstack(Fs), a, b


@functools.lru_cache(maxsize=1)
def _example() -> dict:
    from ohs.inspect.gapflush import measure_gap_flush
    from ohs.inspect.reference import ScanSurface, compare_with_reference, given_alignment, reference_from_arrays
    from ohs.report import section_entry

    rng = np.random.default_rng(7)
    noise = 0.02
    # the fits: a top face, a side face, a boss and a hole, each fitted to noisy points
    u, v = rng.uniform(0, 40, 600), rng.uniform(0, 30, 600)
    top = fit_plane(np.column_stack([u, v, 10.0 + rng.normal(0, noise, 600)]), sigma=noise)
    v2, w2 = rng.uniform(0, 30, 400), rng.uniform(0, 10, 400)
    side = fit_plane(np.column_stack([rng.normal(0, noise, 400), v2, w2]), sigma=noise)
    t, h = rng.uniform(0, 2 * math.pi, 800), rng.uniform(10, 20, 800)
    r = 7.01 + rng.normal(0, noise, 800)
    boss = fit_cylinder(np.column_stack([25 + r * np.cos(t), 15 + r * np.sin(t), h]), sigma=noise, axis0=[0, 0, 1])
    t = rng.uniform(0, 2 * math.pi, 200)
    rh = 5.004 + rng.normal(0, noise, 200)
    hole = fit_circle(np.column_stack([10 + rh * np.cos(t), 15 + rh * np.sin(t), np.full(200, 10.0)]), sigma=noise,
                      normal=[0, 0, 1])
    top_extent = {"kind": "plane", "origin": [0.0, 0.0, 10.0], "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0],
                  "rectangle": {"center": [20.0, 15.0, 10.0], "axes": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                                "half_sizes_mm": [20.0, 15.0]},
                  "area_mm2": 1121.5, "n_outer": 1, "n_holes": 2, "n_points": 412}
    boss_extent = {"kind": "cylinder", "along_mm": [0.0, 10.0], "length_mm": 10.0, "zero": [1.0, 0.0, 0.0],
                   "start_deg": 0.0, "arc_deg": 360.0}
    boss_d, boss_s = boss.diameter_mm, boss.diameter_sigma_mm
    hole_d, hole_s = hole.diameter_mm, hole.diameter_sigma_mm
    features = [
        _feature("f-top", "Top face", "plane", top, extent=top_extent),
        _feature("f-side", "Side face", "plane", side,
                 warnings=["the region touches filled or not measured faces: they are left out"]),
        _feature("f-boss", FIT_HOSTILE, "cylinder", boss, extent=boss_extent, form="boss",
                 nominals=[{"value": "diameter", "nominal": 14.0, "unit": "mm", "word": None, "source": "suggested",
                            "measured": boss_d, "sigma": boss_s, "approved": "2026-11-17T11:00:00",
                            "check_again": False, "check_reason": None}]),
        _feature("f-hole", "Hole A", "hole", hole, mesh_version=2,
                 warnings=["small hole: few points on its edge"],
                 nominals=[{"value": "diameter", "nominal": 10.5, "unit": "mm", "word": None, "source": "entered",
                            "measured": 10.48, "sigma": 0.01, "approved": "2026-11-17T11:05:00", "check_again": True,
                            "check_reason": f"check again: it was approved against 10.48 ± 0.01 mm, and it now measures "
                                            f"{hole_d:.2f} ± {hole_s:.2f} mm. No round value fits."}]),
    ]
    relations = [
        {"id": "r-ts", "name": "Top to side", "a": "f-top", "b": "f-side", "created": CREATED,
         "nominals": [{"value": "angle", "nominal": 90.0, "unit": "deg", "word": "perpendicular", "source": "suggested",
                       "measured": 90.0, "sigma": 0.05, "approved": "2026-11-17T11:10:00"}],
         "move": {"shape": "b", "order": 1, "chosen": "2026-11-17T11:11:00"}},
        {"id": "r-hb", "name": "Hole to boss", "a": "f-hole", "b": "f-boss", "created": CREATED},
    ]
    frame = part_frame({"id": "f-top", "name": "Top face", "fit": features[0]["shape"]},
                       {"id": "f-side", "name": "Side face", "fit": features[1]["shape"]},
                       {"id": "f-hole", "name": "Hole A", "fit": features[3]["shape"]}).to_dict()
    doc = kf.validate({"format_version": 1, "frame": frame, "features": features,
                       "measurements": [{"id": "m-1", "name": "Distance 1", "a": [0.0, 0.0, 10.0], "b": [40.0, 0.0, 10.0],
                                         "mesh_version": 3, "created": CREATED}],
                       "relations": relations})
    doc["mesh_version"] = 3  # the current mesh's version, as GET .../features gives it

    # a section of a 20 mm box through its middle
    Vb, Fb = box(20.0, 16)
    cut = section(Vb, Fb, plane_from_point_normal([0, 0, 0], [0, 1, 0]))
    sections = [section_entry(cut, name=SECTION_HOSTILE,
                              warnings=["the mesh has changed since this section was kept: it was cut again on the "
                                        "current mesh"])]

    # the Check answer on a grid with an uncertain patch and a filled one
    Vg, Fg = grid(41, 31, 1.0)
    S = np.full(len(Vg), 0.02)
    S[(Vg[:, 0] > 5) & (Vg[:, 0] < 12) & (Vg[:, 1] > 5) & (Vg[:, 1] < 12)] = 0.08
    C = Vg[Fg].mean(axis=1)
    filled = (C[:, 0] > 25) & (C[:, 0] < 31) & (C[:, 1] > 20) & (C[:, 1] < 26)
    check = weak_areas(Vg, Fg, S, filled, tolerance_mm=0.1)

    # a sheet compared with its reference, one end lifted 0.6 mm
    Vr, Fr = grid(61, 41, 1.0)
    Vs = Vr.copy()
    Vs[Vs[:, 0] >= 40, 2] += 0.6
    ref = reference_from_arrays(Vr, Fr, name="bracket_v2.stl")
    comparison = compare_with_reference(ScanSurface(Vs, Fr, sigma=np.full(len(Vs), 0.02)), ref,
                                        given_alignment(np.eye(4), "kept from the Align page"), tolerance_mm=0.3)

    # the flush along a seam
    Vp, Fp, ca, cb = _plates()
    flush = measure_gap_flush(Vp, Fp, ca, cb, sigma=np.full(len(Vp), 0.03), step_mm=3.0)

    return {
        "project": {"name": "Door bracket"},
        "scan": {"name": "Scan 3, front"},
        "date": "2026-11-17",
        "app_version": "0.3.0",
        "process": {"mode": "markerless", "quality": "standard", "source": "sim", "scene": "bracket",
                    "frames": {"in_recording": 600, "processed": 200, "tracked": 196, "tracked_fraction": 0.98,
                               "step": 3, "cut": 0},
                    "failures": {"too few points": 4},
                    "imu": {"samples": 12000, "gyro_prior": True, "frames_with_prior": 190},
                    "mesh": {"method": "tsdf", "n_vertices": 402100, "n_faces": 804372, "voxel_mm": 0.5,
                             "bbox_mm": [[0.0, 0.0, 0.0], [120.0, 80.0, 45.0]]},
                    "lasers": {"planes": ["left", "right"], "n_points_exported": 12345},
                    "wall_time_s": 125.4},
        "calibration": {"parts": {"stereo": {"when": "2026-11-15T09:12:00", "summary": "Cameras calibrated, 0.18 px",
                                             "session": "c-1"},
                                  "lasers": {"when": "2026-11-15T09:30:00", "summary": "Both laser lines calibrated",
                                             "session": "c-2"},
                                  "imu": None},
                        "modified": "2026-11-15T09:30:05", "baseline_mm": 60.012, "lasers_missing": [],
                        "last_check": {"when": "2026-11-16T08:00:00", "ok": False,
                                       "verdict": "the board reads 0.4 % large: check the calibration"}},
        "check": check,
        "features": doc,
        "sections": sections,
        "comparison": comparison.to_dict(),
        "gap_flush": flush.to_dict(),
        "pictures": [{"title": PICTURE_HOSTILE, "data": _png(160, 90), "caption": "The part from the front, fits marked."}],
    }


def example() -> dict:
    """The full example (a fresh copy each time: the report must not change what it is given)."""
    return copy.deepcopy(_example())


# --------------------------------------------------------------------------- reading the page back
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Page(HTMLParser):
    """The page as the standard library reads it: tags with their attributes, the text of each
    part (``<header>`` / ``<section>`` by id), the style sheet, and nesting checked as it goes."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, list]] = []
        self.stack: list[str] = []
        self.parts: dict[str, list[str]] = {}
        self.order: list[str] = []
        self.part: str | None = None
        self.part_depth = 0
        self.style: list[str] = []
        self.in_style = False
        self.errors: list[str] = []
        self.headings: list[tuple[str | None, str]] = []
        self.in_heading = False
        self.feed(text)
        self.close()
        if self.stack:
            self.errors.append(f"not closed: {self.stack}")

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))
        if tag in VOID:
            return
        self.stack.append(tag)
        a = dict(attrs)
        if tag in ("section", "header") and a.get("id") and self.part is None:
            self.part, self.part_depth = a["id"], len(self.stack)
            self.order.append(a["id"])
            self.parts[a["id"]] = []
        if tag == "style":
            self.in_style = True
        if tag == "h2":
            self.in_heading = True
            self.headings.append((self.part, ""))

    def handle_startendtag(self, tag, attrs):
        self.tags.append((tag, attrs))
        if tag not in VOID:
            self.errors.append(f"<{tag}/> is not a void element")

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes {self.stack[-1] if self.stack else 'nothing'}")
            return
        if self.part is not None and len(self.stack) == self.part_depth:
            self.part = None
        self.stack.pop()
        if tag == "style":
            self.in_style = False
        if tag == "h2":
            self.in_heading = False

    def handle_data(self, data):
        if self.in_style:
            self.style.append(data)
        elif self.part is not None:
            self.parts[self.part].append(data)
            if self.in_heading:
                p, t = self.headings[-1]
                self.headings[-1] = (p, t + data)

    def text(self, part: str) -> str:
        return re.sub(r"\s+", " ", "".join(self.parts.get(part, [])))


@pytest.fixture(scope="module")
def full():
    from ohs.report import build_html

    data = example()
    text = build_html(data)
    return data, text, Page(text)


def _decimals(sigma: float) -> int:
    """The app's rule (``ui/src/lib/fits.ts``, ``decimals``): a value shows as many decimals as its ±."""
    a = abs(sigma)
    return 1 if a >= 1 else 2 if a >= 0.01 else 3 if a >= 0.001 else 4


def _pm(x: float, s: float) -> str:
    d = _decimals(s)
    return f"{x:.{d}f} ± {s:.{d}f}"


# --------------------------------------------------------------------------- the full example
def test_the_full_example_parses_and_holds_its_parts_in_order(full):
    _, text, page = full
    assert text.startswith("<!DOCTYPE html>")
    assert not page.errors, page.errors
    assert page.order == ["head", "warnings", "meaning", "made", "calibration", "check", "frame", "pictures", "features",
                          "relations", "sections", "comparison", "flush"]
    head = page.text("head")
    for words in ("Door bracket", "Scan 3, front", "2026-11-17", "0.3.0"):
        assert words in head, words


def test_every_name_and_number_is_where_it_belongs(full):
    data, _, page = full
    doc = data["features"]
    feats = page.text("features")
    for f in doc["features"]:
        assert feats.count(f["name"]) == 1, f["name"]
    boss, hole = doc["features"][2]["shape"], doc["features"][3]["shape"]
    assert f"Ø {_pm(boss['diameter_mm'], boss['diameter_sigma_mm'])} mm" in feats
    assert f"Ø {_pm(hole['diameter_mm'], hole['diameter_sigma_mm'])} mm" in feats
    d = _decimals(boss["diameter_sigma_mm"])
    assert f"nominal Ø 14 (measured {boss['diameter_mm']:.{d}f} ± {boss['diameter_sigma_mm']:.{d}f})" in feats
    assert "check again" in feats and "Fitted on an earlier mesh" in feats
    assert "Distance 1" in feats and "40.00 mm" in feats

    rels = page.text("relations")
    by_id = {f["id"]: f for f in doc["features"]}
    for r in doc["relations"]:
        assert rels.count(r["name"]) == 1
        a, b = by_id[r["a"]], by_id[r["b"]]
        rec = relate({"kind": a["kind"], "shape": a["shape"]}, {"kind": b["kind"], "shape": b["shape"]})
        for q in rec.values:
            unit = "°" if q.unit == "deg" else " mm"
            shown = _pm(q.value, q.sigma) if q.sigma is not None else (
                f"{q.value:.2f}" if q.unit == "deg" or abs(q.value) >= 0.1 else f"{q.value:.3f}")
            assert f"{shown}{unit}" in rels, (q.label, shown, rels)
    assert "Side face moves to suit Top face" in rels and "Listed only" in rels
    assert "perpendicular" in rels

    frame = page.text("frame")
    for name in ("Top face", "Side face", "Hole A"):
        assert name in frame

    secs = page.text("sections")
    assert SECTION_HOSTILE in secs and "not 1:1" in secs
    assert page.text("calibration").count("Not assessed") == 1
    check = page.text("check")
    assert "± 0.1 mm" in check and "not measured" in check.lower()
    comp = page.text("comparison")
    assert data["comparison"]["tolerance"]["sentence"] in comp
    assert "bracket_v2.stl" in comp and "not on the CAD model" in comp
    from ohs.inspect.gapflush import GAP_NOTE
    assert GAP_NOTE in page.text("flush")
    assert "Door bracket" not in page.text("meaning")


def test_every_warning_is_in_the_collected_list(full):
    data, _, page = full
    doc = data["features"]
    expected = [w for f in doc["features"] for w in f["warnings"]]
    expected += [n["check_reason"] for f in doc["features"] for n in f.get("nominals", []) if n.get("check_again")]
    expected += list(doc["frame"]["warnings"])
    expected += [w for s in data["sections"] for w in s["warnings"]]
    expected.append(data["calibration"]["last_check"]["verdict"])
    expected += data["comparison"]["reference"]["warnings"] + data["comparison"]["alignment"]["warnings"]
    assert len(expected) >= 5
    listed = page.text("warnings")
    for w in expected:
        assert w in listed, w
    assert "Fitted on an earlier mesh" in listed  # the hole, made on mesh 2 of 3
    # and each stays where it belongs as well
    feats = page.text("features")
    for f in doc["features"]:
        for w in f["warnings"]:
            assert w in feats


def test_pictures_and_drawings_are_inside_the_page(full):
    data, _, page = full
    imgs = [dict(a) for t, a in page.tags if t == "img"]
    srcs = [a["src"] for a in imgs]
    png = [s for s in srcs if s.startswith("data:image/png;base64,")]
    assert len(png) == 1 and base64.b64decode(png[0].split(",", 1)[1]) == data["pictures"][0]["data"]
    assert PICTURE_HOSTILE in page.text("pictures")
    svgs = [s for s in srcs if s.startswith("data:image/svg+xml;base64,")]
    assert len(svgs) == 1
    tree = ET.fromstring(base64.b64decode(svgs[0].split(",", 1)[1]))
    ns = "{http://www.w3.org/2000/svg}"
    assert SECTION_HOSTILE in tree.find(f"{ns}title").text
    assert not [e for e in tree.iter() if e.tag in (f"{ns}script", f"{ns}b", "script", "b")]


# --------------------------------------------------------------------------- absent parts
def test_absent_parts_are_left_out():
    from ohs.report import build_html

    page = Page(build_html({"scan": {"name": "Bare"}}))
    assert not page.errors
    assert page.order == ["head", "warnings", "meaning"]
    assert "None of the parts in this report" in page.text("warnings")
    for part, heading in page.headings:
        rest = page.text(part).replace(heading, "", 1).strip()
        assert rest, f"the heading {heading!r} has nothing under it"


def test_each_part_alone_and_calibration_health_not_assessed():
    from ohs.report import build_html

    data = example()
    for key, part in (("process", "made"), ("calibration", "calibration"), ("check", "check"),
                      ("comparison", "comparison"), ("gap_flush", "flush"), ("sections", "sections"),
                      ("pictures", "pictures")):
        page = Page(build_html({key: data[key]}))
        assert not page.errors, (key, page.errors)
        assert part in page.order and len(page.order) <= 4, (key, page.order)
        for p, heading in page.headings:
            assert page.text(p).replace(heading, "", 1).strip(), (key, heading)
    cal = Page(build_html({"calibration": {"parts": {"stereo": None, "lasers": None, "imu": None}}}))
    assert "Not assessed" in cal.text("calibration")
    given = Page(build_html({"calibration": {**data["calibration"],
                                             "health": {"state": "check", "part": "lasers",
                                                        "reason": "the left laser line reads 0.2 mm off the depth"}}}))
    t = given.text("calibration")
    assert "Not assessed" not in t and "Check recommended" in t and "0.2 mm off the depth" in given.text("warnings")
    # FEATURE_PLAN H4: the scan's own status (health_status): what was not judged is said, never called fine, and the
    # limits are provisional (said once)
    own = Page(build_html({"calibration": {"health": {
        "state": "valid", "part": None, "part_label": None, "provisional": True, "source": "scan",
        "reason": "This scan shows nothing off for the cameras.", "not_judged": ["lasers", "imu"],
        "note": "This scan was processed without a GPU, so only the cameras could be judged."}}}))
    t = own.text("calibration")
    assert "Valid" in t and "nothing off for the cameras" in t and "without a GPU" in t
    assert "Not judged: the lasers and the motion sensor" in t and t.count("provisional") == 1
    assert "nothing off for the cameras" not in own.text("warnings")  # a valid status is no warning
    # fits alone: features without relations, sections or a frame
    only = Page(build_html({"features": {**data["features"], "relations": [], "frame": None}}))
    assert "features" in only.order and "relations" not in only.order and "frame" not in only.order


# --------------------------------------------------------------------------- safe and self-contained
def test_nothing_reaches_for_the_network_and_names_stay_text(full):
    _, text, page = full
    low = text.lower()
    assert "<script" not in low
    for name in (FIT_HOSTILE, SECTION_HOSTILE, PICTURE_HOSTILE):
        assert name not in text  # never as it is: always escaped
    assert FIT_HOSTILE in page.text("features")  # ... and shown as text
    banned = {"script", "link", "iframe", "object", "embed", "base", "form", "frame", "frameset", "audio", "video",
              "source", "svg", "use", "image", "foreignobject"}
    for tag, attrs in page.tags:
        assert tag not in banned, tag
        for name, value in attrs:
            assert not name.startswith("on"), (tag, name)
            assert name != "style", tag
            value = value or ""
            if tag == "img" and name == "src":
                assert value.startswith(("data:image/png;base64,", "data:image/svg+xml;base64,", "data:image/jpeg;base64,",
                                         "data:image/gif;base64,", "data:image/webp;base64,"))
                continue
            if name == "href":
                assert value.startswith("#"), value
            for bad in ("http:", "https:", "//", "url(", "javascript:"):
                assert bad not in value.lower(), (tag, name, value)
    style = "".join(page.style).lower()
    for bad in ("url(", "@import", "http", "//", "expression("):
        assert bad not in style, bad
    # the embedded drawing has no script, no link and no handler either
    for tag, attrs in page.tags:
        a = dict(attrs)
        if tag == "img" and a["src"].startswith("data:image/svg+xml;base64,"):
            svg = base64.b64decode(a["src"].split(",", 1)[1]).decode("utf-8")
            assert "<script" not in svg.lower()
            for el in ET.fromstring(svg).iter():
                assert el.tag.rsplit("}", 1)[-1] not in ("script", "a", "image", "use", "foreignObject"), el.tag
                assert not [k for k in el.attrib if k.rsplit("}", 1)[-1].startswith("on") or "href" in k], el.attrib
    csp = [dict(a) for t, a in page.tags if t == "meta" and dict(a).get("http-equiv") == "Content-Security-Policy"]
    assert csp and "default-src 'none'" in csp[0]["content"]


def test_print_styles_are_there(full):
    _, _, page = full
    style = "".join(page.style)
    m = re.search(r"@media print\s*\{", style)
    assert m
    printed = style[m.end():]
    assert re.search(r"@page\s*\{[^}]*size\s*:", printed) and re.search(r"@page\s*\{[^}]*margin\s*:", printed)
    assert re.search(r"counter\(page\)", printed)
    assert re.search(r"tr[^{]*\{[^}]*break-inside\s*:\s*avoid", printed)
    assert re.search(r"thead\s*\{[^}]*display\s*:\s*table-header-group", style)
    assert re.search(r"color\s*:\s*#000", printed) and re.search(r"background\s*:\s*#fff", printed)


def test_the_same_data_gives_the_same_bytes():
    from ohs.report import build_html

    a = build_html(example()).encode("utf-8")
    b = build_html(example()).encode("utf-8")
    assert a == b
    data = example()
    before = copy.deepcopy(data)
    build_html(data)
    assert repr(data) == repr(before)  # what was given is left as it was
    data["date"] = "2026-11-18"
    assert build_html(data).encode("utf-8") != a


def test_digits_follow_the_plus_minus():
    from ohs.report.numbers import pm

    assert pm(14.0234, 0.031, "mm") == "14.02 ± 0.03 mm"
    assert pm(14.0234, 0.0042, "mm") == "14.023 ± 0.004 mm"
    assert pm(14.0234, 0.00042, "mm") == "14.0234 ± 0.0004 mm"
    assert pm(123.456, 1.24, "mm") == "123.5 ± 1.2 mm"
    assert pm(89.7012, 0.052, "deg") == "89.70 ± 0.05°"
    assert pm(-0.004, 0.02, "mm") == "0.00 ± 0.02 mm"  # never "-0.00"
    assert pm(14.0234, None, "mm") == "14.02 mm"
    assert pm(0.0456, None, "mm") == "0.046 mm"
