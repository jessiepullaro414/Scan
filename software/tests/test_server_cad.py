"""The kept fits for CAD over the API (FEATURE_PLAN.md C2; APP_API.md "Kept features", "For CAD"):
``GET .../features/cad`` says what would be exported, ``GET .../features/cad/fits.step`` and
``.../fits.FCMacro`` are the STEP file and the FreeCAD macro of the scan's kept fits, in the part's
frame when the scan has one.

The files must equal what the engine's writers make of the same kept fits
(``ohs.cad.step.step_text`` and ``ohs.cad.fcmacro.macro_text`` of ``ohs.cad.shapes.cad_shapes``),
with each plane's stored outline on request, and nothing on disk may change. Uses the 10-frame
sphere scan of ``test_server_process_studio.py`` (its ``env`` fixture), tracked once at preview
quality; the kept fits are the box and the plate of ``tools/step_samples.py``, written straight
into ``features.json`` (as ``test_server_frame.py`` does), fitted on the current mesh version.
"""
from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ohs.measure.features as kf  # noqa: E402
from ohs.cad.fcmacro import macro_text  # noqa: E402
from ohs.cad.shapes import cad_shapes  # noqa: E402
from ohs.cad.step import step_text  # noqa: E402
from step_reader import read  # noqa: E402
from test_server_process_studio import T, base_url, env, err, get, post, run_job  # noqa: E402,F401
from tools import step_samples  # noqa: E402

KIND = {"plane": "plane", "cylinder": "cylinder", "hole": "hole"}


def scan_dir(e: dict) -> Path:
    return e["root"] / "projects" / e["pid"] / "scans" / e["sid"]


def version(e: dict) -> int:
    return int(get(base_url(e) + "/process").json()["current"]["version"])


def title(e: dict) -> str:
    """The files are named after the part: the project's name."""
    return get(f"{e['url']}/api/projects/{e['pid']}").json()["name"]


def record(f: dict, v: int) -> dict:
    """A sample feature as features.json keeps a kept fit."""
    shape = f["shape"]
    point = shape.get("point", shape.get("center"))
    return {**copy.deepcopy(f), "seed": {"kind": f["kind"], "face": 0, "point": point, "tolerance_mm": None},
            "n_faces": 100, "tolerance_mm": None, "mean_sigma_mm": None, "correlated_points": None, "warnings": [],
            "mesh_version": v, "form": None, "created": "2026-11-17T12:00:00"}


def keep(e: dict, sample: str, frame: bool = False) -> dict:
    """Write a sample's fits (and its outlines, and with ``frame`` its part's frame) as kept fits."""
    s = step_samples.sample_set()[sample]
    sd = scan_dir(e)
    v = version(e)
    doc = kf.empty()
    doc["features"] = [record(f, v) for f in s["features"]]
    doc["frame"] = s["frame"] if frame else None
    kf.save(sd, doc)
    for fid, ol in s["outlines"].items():
        kf.save_outline(sd, fid, {k: ol[k] for k in ("kind", "origin", "u", "v", "outer", "holes")}, v)
    return s


def engine(e: dict, boundary: str = "rectangle", frame: bool = True):
    """What the engine's writers make of the kept fits as stored: (STEP text, macro text, shapes)."""
    sd = scan_dir(e)
    doc = kf.load(sd)
    outlines = {f["id"]: kf.load_outline(sd, f["id"]) for f in doc["features"] if f["kind"] == "plane"}
    outlines = {k: o for k, o in outlines.items() if o is not None}
    shapes = cad_shapes(doc["features"], outlines=outlines, frame=doc["frame"] if frame else None, boundary=boundary)
    return step_text(shapes, title=title(e)), macro_text(shapes, title=title(e)), shapes


def on_disk(e: dict) -> dict:
    """Every file of the scan's folder and its hash."""
    d = scan_dir(e)
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def tracked(env):  # noqa: F811 (the fixture imported above)
    b = base_url(env)
    err(get(b + "/features/cad/fits.step"), 409, "no fitted shapes")  # before there is a mesh too
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    return env


@pytest.fixture()
def clean(tracked):
    sd = scan_dir(tracked)
    kf.save(sd, kf.empty())
    yield tracked
    for f in kf.load(sd)["features"]:
        kf.remove_outline(sd, f["id"])
    kf.save(sd, kf.empty())


def cad(e: dict, name: str = "", **query):
    path = base_url(e) + "/features/cad" + (f"/{name}" if name else "")
    return requests.get(path, params=query or None, timeout=T)


# --------------------------------------------------------------------------- tests
def test_no_kept_fits_is_a_clear_message(clean):
    for name in ("fits.step", "fits.FCMacro"):
        r = err(cad(clean, name), 409, "no fitted shapes")
        assert "fit" in r["hint"].lower()
    s = cad(clean)
    assert s.status_code == 200, s.text
    s = s.json()
    assert s["n_fits"] == 0 and s["reason"] and "no fitted shapes" in s["reason"].lower()
    assert s["counts"] == {"plane": 0, "cylinder": 0, "hole": 0, "point": 0}


def test_step_in_the_scans_frame_equals_the_writer(clean):
    keep(clean, "box_with_hole_and_boss.step", frame=False)
    want_step, want_macro, shapes = engine(clean)
    r = cad(clean, "fits.step")
    assert r.status_code == 200, r.text
    assert r.content == want_step.encode("ascii")
    assert r.headers["content-type"].startswith("model/step")
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;") and cd.endswith('.step"') and "fits" in cd
    assert int(r.headers["x-ohs-mesh-version"]) == version(clean)
    assert not shapes.in_part_frame
    m = cad(clean, "fits.FCMacro")
    assert m.status_code == 200, m.text
    assert m.content == want_macro.encode("ascii")
    assert m.headers["content-disposition"].endswith('.FCMacro"')
    assert m.headers["content-disposition"] != cd  # the two cannot be mistaken for each other
    s = cad(clean).json()
    assert s["frame"] == "scan" and "scan's own axes" in s["frame_label"]
    assert s["counts"] == {"plane": 2, "cylinder": 1, "hole": 1, "point": 0} and s["n_fits"] == 4
    assert s["notes"] == shapes.notes and s["warnings"] == [] and s["reason"] is None
    assert s["boundary"] == "rectangle"
    assert s["step_bytes"] == len(want_step) and s["macro_bytes"] == len(want_macro)
    assert s["filenames"]["step"].endswith(".step") and s["filenames"]["macro"].endswith(".FCMacro")


def test_with_a_frame_the_files_are_in_the_parts_frame(clean):
    s = keep(clean, "box_with_hole_and_boss.step", frame=True)
    want_step, want_macro, shapes = engine(clean)
    assert shapes.in_part_frame
    r = cad(clean, "fits.step")
    assert r.status_code == 200 and r.content == want_step.encode("ascii")
    assert cad(clean, "fits.FCMacro").content == want_macro.encode("ascii")
    hole = next(f for f in s["features"] if f["id"] == "f-hole")
    circle = next(x for x in read(r.text)["products"][hole["name"]] if x["type"] == "circle")
    assert max(abs(float(c)) for c in circle["center"]) < 1e-9  # the hole is the origin
    assert "X axis" in read(r.text)["products"]
    summary = cad(clean).json()
    assert summary["frame"] == "part" and "part's axes" in summary["frame_label"]
    assert summary["warnings"] == []
    # an out-of-date frame is used as it is, and said so
    sd = scan_dir(clean)
    doc = kf.load(sd)
    doc["features"][2]["shape"]["center"][0] += 0.5  # the origin hole changed since
    kf.save(sd, doc)
    summary = cad(clean).json()
    assert summary["frame"] == "part" and any("out of date" in w for w in summary["warnings"])
    assert cad(clean, "fits.step").content == engine(clean)[0].encode("ascii")
    # a frame this version cannot read: the scan's own frame, said so
    doc["frame"] = {"format_version": 99, "matrix": "from the future"}
    kf.save(sd, doc)
    summary = cad(clean).json()
    assert summary["frame"] == "scan" and any("cannot read" in w for w in summary["warnings"])
    assert cad(clean, "fits.step").content == engine(clean, frame=False)[0].encode("ascii")


def test_the_outline_option(clean):
    keep(clean, "plate_outline.step")
    rect_step, _, _ = engine(clean, "rectangle")
    out_step, out_macro, shapes = engine(clean, "outline")
    assert rect_step != out_step
    assert cad(clean, "fits.step").content == rect_step.encode("ascii")
    assert cad(clean, "fits.step", boundary="rectangle").content == rect_step.encode("ascii")
    assert cad(clean, "fits.step", boundary="outline").content == out_step.encode("ascii")
    assert cad(clean, "fits.FCMacro", boundary="outline").content == out_macro.encode("ascii")
    s = cad(clean, boundary="outline").json()
    assert s["boundary"] == "outline" and s["notes"] == shapes.notes
    assert s["counts"] == {"plane": 1, "cylinder": 0, "hole": 2, "point": 0}
    plate = shapes.shapes[0]
    assert s["outline"] == {"planes": 1, "points": len(plate.outer) + sum(len(h) for h in plate.holes),
                            "holes_kept": 2, "holes_left_out": 3}
    assert s["step_bytes"] == len(out_step) > cad(clean).json()["step_bytes"]
    err(cad(clean, "fits.step", boundary="lasso"), 422)


def test_fits_made_on_an_earlier_mesh_are_exported_and_said_so(clean):
    keep(clean, "box_with_hole_and_boss.step")
    sd = scan_dir(clean)
    doc = kf.load(sd)
    doc["features"][3]["mesh_version"] = version(clean) - 1  # the boss
    kf.save(sd, doc)
    want_step, _, _ = engine(clean)
    assert cad(clean, "fits.step").content == want_step.encode("ascii")
    s = cad(clean).json()
    assert s["stale"] == ["f-boss"] and s["counts"]["cylinder"] == 1
    assert len(s["warnings"]) == 1 and "Boss" in s["warnings"][0] and "earlier mesh" in s["warnings"][0]


def test_unknown_project_scan_or_file(clean):
    keep(clean, "box_with_hole_and_boss.step")
    for url in (base_url(clean, pid="nope"), base_url(clean, sid="nope")):
        for tail in ("/features/cad", "/features/cad/fits.step", "/features/cad/fits.FCMacro"):
            err(get(url + tail), 404)
    err(cad(clean, "fits.iges"), 404, "kind of CAD file")


def test_nothing_on_disk_changes_and_other_downloads_stay_the_same(clean):
    keep(clean, "plate_outline.step")
    b = base_url(clean)
    urls = [f"{b}/files/mesh.ply", f"{b}/files/mesh.stl", f"{b}/features", f"{b}/features/f-plate/outline"]
    before = {u: get(u).content for u in urls}
    files = on_disk(clean)
    for boundary in ("rectangle", "outline"):
        for name in ("", "fits.step", "fits.FCMacro"):
            assert cad(clean, name, boundary=boundary).status_code == 200
    assert on_disk(clean) == files
    assert {u: get(u).content for u in urls} == before
