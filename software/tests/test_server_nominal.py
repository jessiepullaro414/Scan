"""Approved nominal values over the API (FEATURE_PLAN.md C3, the app part; APP_API.md "Kept
features", "Nominal values", "For CAD").

* ``GET .../features/{id}/nominal`` offers what ``ohs.cad.nominal`` offers for the same kept record
  (a fit's ``form`` from its record, the poses read from the scan's processing report);
* ``POST`` approves the suggested value or one entered by hand (a senseless one is refused, 422),
  ``DELETE .../nominal/{value}`` removes it; the approval is kept in ``features.json`` beside the
  measured values, which never change, and survives a reload and a rename; deleting the fit (or
  the relation) takes it along;
* after the measurement changes (a fit fitted again), an approval stays; one the new measurement
  no longer allows by the engine's rule is marked "check again" with the old and the new measured
  value; nothing is changed without a request;
* the CAD export applies approved sizes (a cylinder's or a hole's diameter) to the STEP file and
  the macro and lists every approval in their notes; relations are listed, not applied;
  ``measured_only`` gives exactly the files of before; a file written before this task loads and
  exports as before.

Uses the 10-frame sphere scan of ``test_server_process_studio.py`` (its ``env`` fixture), tracked
once at preview quality. A ball on a table has no boss or hole, so the kept fits are written
straight into ``features.json`` (as ``test_server_cad.py`` does); "fit again" runs the real route
with the fit itself replaced by a stand-in that answers a new measurement.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

import ohs.measure.features as kf  # noqa: E402
from ohs.cad.nominal import suggest_fit, suggest_relation  # noqa: E402
from ohs.measure.relate import relate  # noqa: E402
from step_reader import read  # noqa: E402
from test_server_cad import engine, keep, on_disk, scan_dir, version  # noqa: E402
from test_server_process_studio import T, base_url, env, err, get, post, run_job  # noqa: E402,F401

TILT = math.radians(0.05)


def cyl_shape(d: float, sd: float) -> dict:
    return {"kind": "cylinder", "point": [0.0, 0.0, 0.0], "axis": [0.0, 0.0, 1.0], "radius_mm": d / 2, "diameter_mm": d,
            "rms_mm": 0.02, "n": 800, "radius_sigma_mm": sd / 2, "diameter_sigma_mm": sd, "axis_sigma_deg": 0.3,
            "point_sigma_mm": 0.01}


def hole_shape(d: float, sd: float) -> dict:
    return {"kind": "circle", "center": [30.0, 0.0, 10.0], "normal": [0.0, 0.0, 1.0], "radius_mm": d / 2,
            "diameter_mm": d, "rms_mm": 0.02, "n": 200, "radius_sigma_mm": sd / 2, "diameter_sigma_mm": sd,
            "center_sigma_mm": 0.02}


def plane_shape(normal, point) -> dict:
    return {"kind": "plane", "point": list(point), "normal": list(normal), "rms_mm": 0.02, "flatness_mm": 0.1, "n": 900,
            "normal_sigma_deg": 0.05, "offset_sigma_mm": 0.01}


CYL_EXTENT = {"kind": "cylinder", "along_mm": [-5.0, 5.0], "length_mm": 10.0, "zero": [1.0, 0.0, 0.0], "start_deg": 0.0,
              "arc_deg": 360.0}
HOLE_EXTENT = {"kind": "hole", "n_edge": 3, "edge": [[35.0, 0.0, 10.0], [27.5, 4.33, 10.0], [27.5, -4.33, 10.0]]}


def plane_extent(point, u) -> dict:
    """A 40 x 30 mm rectangle round the plane's point (so "fit again" has no extent to measure)."""
    return {"kind": "plane", "origin": list(point), "u": list(u), "v": [0.0, 1.0, 0.0],
            "rectangle": {"center": list(point), "axes": [list(u), [0.0, 1.0, 0.0]], "half_sizes_mm": [20.0, 15.0]},
            "area_mm2": 1200.0, "n_outer": 1, "n_holes": 0, "n_points": 4}


def record(fid: str, name: str, kind: str, shape: dict, v: int, form=None, extent=None, seed_point=None) -> dict:
    point = seed_point or shape.get("point", shape.get("center"))
    return {"id": fid, "name": name, "kind": kind, "shape": shape,
            "seed": {"kind": kind, "face": 0, "point": list(point), "tolerance_mm": None}, "n_faces": 100,
            "tolerance_mm": None, "mean_sigma_mm": None, "correlated_points": None, "warnings": [], "mesh_version": v,
            "extent": extent, "form": form, "created": "2026-11-17T12:00:00"}


def keep_set(e: dict, v: int | None = None, seed_point=None) -> dict:
    """A boss Ø 14.02 ± 0.03, a hole Ø 10.04 ± 0.03, two planes 0.05° apart and their relation."""
    v = version(e) if v is None else v
    doc = kf.empty()
    doc["features"] = [
        record("f-boss", "Boss", "cylinder", cyl_shape(14.02, 0.03), v, form="boss", extent=CYL_EXTENT, seed_point=seed_point),
        record("f-hole", "Hole", "hole", hole_shape(10.04, 0.03), v, extent=HOLE_EXTENT, seed_point=seed_point),
        record("f-top", "Top", "plane", plane_shape([0.0, 0.0, 1.0], [0.0, 0.0, 10.0]), version(e),
               extent=plane_extent([0.0, 0.0, 10.0], [1.0, 0.0, 0.0])),
        record("f-bottom", "Bottom", "plane", plane_shape([math.sin(TILT), 0.0, math.cos(TILT)], [0.0, 0.0, -0.02]),
               version(e), extent=plane_extent([0.0, 0.0, -0.02], [math.cos(TILT), 0.0, -math.sin(TILT)])),
    ]
    doc["relations"] = [{"id": "r-tb", "name": "Top to bottom", "a": "f-top", "b": "f-bottom",
                         "created": "2026-11-17T12:00:00"}]
    return kf.save(scan_dir(e), doc)


def nominal_url(e: dict, item: str, value: str = "") -> str:
    return base_url(e) + f"/features/{item}/nominal" + (f"/{value}" if value else "")


def approve(e: dict, item: str, value: str, nominal: float, source: str = "suggested"):
    return post(nominal_url(e, item), {"value": value, "nominal": nominal, "source": source})


def delete(url: str):
    return requests.delete(url, timeout=T)


def by_id(items: list[dict], fid: str) -> dict:
    return next(x for x in items if x["id"] == fid)


def file_bytes(e: dict) -> bytes:
    return (scan_dir(e) / kf.FILE_NAME).read_bytes()


def measured_part(doc: dict) -> dict:
    """``features.json`` without the approvals: what was measured."""
    out = copy.deepcopy(doc)
    for key in ("features", "relations"):
        for it in out[key]:
            it.pop("nominals", None)
    return out


@pytest.fixture(scope="module")
def tracked(env):  # noqa: F811 (the fixture imported above)
    run_job(env, post(base_url(env) + "/process/track", {"quality": "preview"}), "process_track")
    return env


@pytest.fixture()
def clean(tracked):
    sd = scan_dir(tracked)
    kf.save(sd, kf.empty())
    yield tracked
    for f in kf.load(sd)["features"]:
        kf.remove_faces(sd, f["id"])
        kf.remove_outline(sd, f["id"])
    kf.save(sd, kf.empty())


# --------------------------------------------------------------------------- offers
def test_the_offers_are_the_engines_for_the_same_record(clean):
    doc = keep_set(clean)
    feats = {f["id"]: f for f in doc["features"]}
    for fid in ("f-boss", "f-hole"):
        r = get(nominal_url(clean, fid))
        assert r.status_code == 200, r.text
        ans = r.json()
        assert ans["id"] == fid and ans["kind"] == "fit" and ans["nominals"] == []
        assert ans["poses"] == "tracked" and ans["poses_reason"]
        assert ans["offers"] == [o.to_dict() for o in suggest_fit(feats[fid], poses="tracked")]
    boss = get(nominal_url(clean, "f-boss")).json()
    assert boss["form"] == "boss" and [s["nominal"] for s in boss["offers"][0]["suggestions"]] == [14.0]
    assert [s["nominal"] for s in get(nominal_url(clean, "f-hole")).json()["offers"][0]["suggestions"]] == [10.0]
    radius = get(nominal_url(clean, "f-boss") + "?value=radius").json()
    assert radius["offers"] == [o.to_dict() for o in suggest_fit(feats["f-boss"], poses="tracked", value="radius")]
    # a relation: the engine's relation of the two kept shapes, with both fits' warnings
    rel = relate({"kind": "plane", "shape": feats["f-top"]["shape"]}, {"kind": "plane", "shape": feats["f-bottom"]["shape"]})
    ans = get(nominal_url(clean, "r-tb")).json()
    assert ans["kind"] == "relation"
    assert ans["offers"] == [o.to_dict() for o in suggest_relation(rel.to_dict(), poses="tracked")]
    angle = next(o for o in ans["offers"] if o["value"] == "angle")
    assert angle["suggestions"][0]["word"] == "parallel"
    # a plane alone has no size: no offers, not an error
    assert get(nominal_url(clean, "f-top")).json()["offers"] == []
    err(get(nominal_url(clean, "f-nope")), 404)
    err(get(nominal_url(clean, "f-boss") + "?value=angle"), 422)


def test_the_poses_come_from_the_processing_report(clean):
    keep_set(clean)
    doc = kf.load(scan_dir(clean))
    boss = by_id(doc["features"], "f-boss")
    report = scan_dir(clean) / "result" / "report.json"
    original = report.read_bytes()
    try:
        rep = json.loads(original)
        rep["poses"] = "given"
        rep["ground_truth"] = {"n": 8, "rot_deg": {"n": 8, "max": 0.0}, "trans_mm": {"n": 8, "max": 0.0},
                               "rms_trans_mm": 0.0}
        report.write_text(json.dumps(rep), encoding="utf-8")
        ans = get(nominal_url(clean, "f-boss")).json()
        assert ans["poses"] == "true" and "true path" in ans["poses_reason"]
        assert ans["offers"] == [o.to_dict() for o in suggest_fit(boss, poses="true")]
        rep["ground_truth"]["trans_mm"]["max"] = 1.5  # given, but not the true poses: straightened tracked ones
        report.write_text(json.dumps(rep), encoding="utf-8")
        ans = get(nominal_url(clean, "f-boss")).json()
        assert ans["poses"] == "tracked" and "given" in ans["poses_reason"]
        report.unlink()
        ans = get(nominal_url(clean, "f-boss")).json()
        assert ans["poses"] == "tracked" and "no processing report" in ans["poses_reason"]
    finally:
        report.write_bytes(original)


# --------------------------------------------------------------------------- approve, remove
def test_approve_the_suggested_value_or_an_entered_one_and_remove_it(clean):
    keep_set(clean)
    before = kf.load(scan_dir(clean))
    r = approve(clean, "f-boss", "diameter", 14.0)
    assert r.status_code == 200, r.text
    (n,) = r.json()["nominals"]
    assert n["value"] == "diameter" and n["nominal"] == 14.0 and n["source"] == "suggested" and n["unit"] == "mm"
    assert n["measured"] == pytest.approx(14.02) and n["sigma"] == pytest.approx(0.03) and n["approved"]
    assert n["check_again"] is False and n["word"] is None
    stored = by_id(kf.load(scan_dir(clean))["features"], "f-boss")["nominals"]
    assert stored == [{k: n[k] for k in ("value", "nominal", "unit", "word", "source", "measured", "sigma", "approved")}]
    # the measured values do not change
    assert measured_part(kf.load(scan_dir(clean))) == measured_part(before)
    # an entered value needs no offer and replaces the approval of the same size (a radius too)
    r = approve(clean, "f-boss", "radius", 7.05, "entered")
    assert r.status_code == 200, r.text
    (n,) = r.json()["nominals"]
    assert n["value"] == "radius" and n["nominal"] == 7.05 and n["source"] == "entered" and n["measured"] == pytest.approx(7.01)
    # a suggested value must be one offered now
    err(approve(clean, "f-boss", "diameter", 14.5), 422, "offered")
    # senseless values
    err(approve(clean, "f-boss", "diameter", -1.0, "entered"), 422)
    err(approve(clean, "f-boss", "diameter", 0.0, "entered"), 422)
    err(approve(clean, "f-boss", "diameter", 1e9, "entered"), 422)
    err(approve(clean, "f-boss", "angle", 90.0, "entered"), 422)
    err(approve(clean, "f-top", "diameter", 14.0, "entered"), 422, "plane")
    err(approve(clean, "r-tb", "angle", 120.0, "entered"), 422)
    err(approve(clean, "r-tb", "centre_distance", 3.0, "entered"), 422)
    err(post(nominal_url(clean, "f-boss"), {"value": "diameter", "nominal": 14.0, "source": "rounded"}), 422)
    err(approve(clean, "f-nope", "diameter", 14.0, "entered"), 404)
    # a relation's word
    r = approve(clean, "r-tb", "angle", 0.0)
    assert r.status_code == 200, r.text
    (n,) = r.json()["nominals"]
    assert n["word"] == "parallel" and n["unit"] == "deg" and n["source"] == "suggested"
    r = approve(clean, "r-tb", "distance", 10.0, "entered")
    assert len(r.json()["nominals"]) == 2
    # GET .../features shows them
    s = get(base_url(clean) + "/features").json()
    assert [x["value"] for x in by_id(s["features"], "f-boss")["nominals"]] == ["radius"]
    assert [x["value"] for x in by_id(s["relations"], "r-tb")["nominals"]] == ["angle", "distance"]
    assert by_id(s["features"], "f-top")["nominals"] == [] and by_id(s["features"], "f-hole")["nominals"] == []
    # remove
    assert delete(nominal_url(clean, "f-boss", "radius")).status_code == 204
    err(delete(nominal_url(clean, "f-boss", "radius")), 404)
    assert "nominals" not in by_id(kf.load(scan_dir(clean))["features"], "f-boss")  # back to the file's old shape
    assert delete(nominal_url(clean, "r-tb", "angle")).status_code == 204
    assert [x["value"] for x in get(nominal_url(clean, "r-tb")).json()["nominals"]] == ["distance"]
    assert measured_part(kf.load(scan_dir(clean))) == measured_part(before)


def test_an_approval_survives_a_reload_and_a_rename_and_goes_with_its_item(clean):
    keep_set(clean)
    assert approve(clean, "f-boss", "diameter", 14.0).status_code == 200
    assert approve(clean, "f-hole", "diameter", 10.0).status_code == 200
    assert approve(clean, "r-tb", "angle", 0.0).status_code == 200
    r = requests.patch(base_url(clean) + "/features/f-boss", json={"name": "Boss Ø 14"}, timeout=T)
    assert r.status_code == 200, r.text
    s = get(base_url(clean) + "/features").json()
    assert by_id(s["features"], "f-boss")["name"] == "Boss Ø 14"
    assert by_id(s["features"], "f-boss")["nominals"][0]["nominal"] == 14.0
    assert kf.load(scan_dir(clean))["features"][0]["nominals"][0]["nominal"] == 14.0
    # deleting a fit takes its approvals, and a relation it was in takes its own
    assert delete(base_url(clean) + "/features/f-boss").status_code == 204
    assert delete(base_url(clean) + "/features/f-top").status_code == 204
    doc = kf.load(scan_dir(clean))
    assert [f["id"] for f in doc["features"]] == ["f-hole", "f-bottom"] and doc["relations"] == []
    assert by_id(doc["features"], "f-hole")["nominals"][0]["nominal"] == 10.0
    err(get(nominal_url(clean, "f-boss")), 404)


# --------------------------------------------------------------------------- the measurement changes
def table_point(e: dict) -> list[float]:
    """A point on the scan's table (the surface "fit again" finds again)."""
    v, V, F, _, _ = e["studio"].processing.mesh_arrays(e["pid"], e["sid"])
    C = V[F].mean(axis=1)
    far = np.flatnonzero(C[:, 2] >= np.percentile(C[:, 2], 90))
    return [float(x) for x in C[far[np.argmin(np.linalg.norm(C[far] - C[far].mean(axis=0), axis=1))]]]


def test_after_fit_again_an_approval_stays_and_is_marked_when_the_new_measurement_does_not_allow_it(clean, monkeypatch):
    v = version(clean)
    keep_set(clean, v=v - 1, seed_point=table_point(clean))  # the boss and the hole made on an earlier mesh
    assert approve(clean, "f-boss", "diameter", 14.0).status_code == 200
    assert approve(clean, "f-hole", "diameter", 10.0).status_code == 200
    approvals = {f["id"]: f["nominals"] for f in kf.load(scan_dir(clean))["features"] if "nominals" in f}

    new = {"cylinder": (cyl_shape(14.03, 0.03), CYL_EXTENT, "boss"), "hole": (hole_shape(10.40, 0.03), HOLE_EXTENT, None)}

    def fit_again(pid, sid, kind, face, point, tolerance_mm, version_=None, with_extent=False, with_form=False):
        shape, extent, form = new[kind]
        return {"kind": kind, "shape": shape, "faces": base64.b64encode(np.arange(5, dtype="<u4").tobytes()).decode("ascii"),
                "n_faces": 5, "tolerance_mm": None, "mean_sigma_mm": None, "correlated_points": None, "warnings": [],
                "version": v, "extent": extent, "form": form}

    monkeypatch.setattr(clean["studio"].processing, "fit", fit_again)
    r = post(base_url(clean) + "/features/refit")
    assert r.status_code == 200, r.text
    assert sorted(r.json()["refitted"]) == ["f-boss", "f-hole"]
    doc = kf.load(scan_dir(clean))
    assert by_id(doc["features"], "f-hole")["shape"]["diameter_mm"] == 10.40  # fitted again
    assert {f["id"]: f["nominals"] for f in doc["features"] if "nominals" in f} == approvals  # kept as approved
    stored = file_bytes(clean)
    s = get(base_url(clean) + "/features").json()
    boss = by_id(s["features"], "f-boss")["nominals"][0]
    assert boss["check_again"] is False and boss["measured_now"] == pytest.approx(14.03)  # 14 still fits
    hole = by_id(s["features"], "f-hole")["nominals"][0]
    assert hole["check_again"] is True and hole["nominal"] == 10.0
    assert hole["measured"] == pytest.approx(10.04) and hole["measured_now"] == pytest.approx(10.40)
    assert "10.04" in hole["check_reason"] and "10.40" in hole["check_reason"]
    assert get(nominal_url(clean, "f-hole")).json()["nominals"][0]["check_again"] is True
    assert file_bytes(clean) == stored  # reading changed nothing
    # approving again clears the mark; so does removing it
    assert approve(clean, "f-hole", "diameter", 10.4, "entered").json()["nominals"][0]["check_again"] is False
    assert delete(nominal_url(clean, "f-hole", "diameter")).status_code == 204


def test_a_relation_approval_is_marked_when_a_fit_changes_and_not_when_it_still_fits(clean):
    doc = keep_set(clean)
    assert approve(clean, "r-tb", "angle", 0.0).status_code == 200
    assert by_id(get(base_url(clean) + "/features").json()["relations"], "r-tb")["nominals"][0]["check_again"] is False
    doc = kf.load(scan_dir(clean))
    tilt = math.radians(1.0)  # the bottom fitted again a degree off: no longer parallel within its ±
    by_id(doc["features"], "f-bottom")["shape"]["normal"] = [math.sin(tilt), 0.0, math.cos(tilt)]
    kf.save(scan_dir(clean), doc)
    (n,) = by_id(get(base_url(clean) + "/features").json()["relations"], "r-tb")["nominals"]
    assert n["check_again"] is True and n["measured_now"] == pytest.approx(1.0, abs=1e-6) and n["word"] == "parallel"


# --------------------------------------------------------------------------- for CAD
def cad(e: dict, name: str = "", **query):
    return requests.get(base_url(e) + "/features/cad" + (f"/{name}" if name else ""), params=query or None, timeout=T)


def test_the_cad_files_carry_the_approved_sizes_and_list_every_approval(clean):
    keep_set(clean)
    plain_step, plain_macro = cad(clean, "fits.step").content, cad(clean, "fits.FCMacro").content
    plain_summary = cad(clean).json()
    assert approve(clean, "f-boss", "diameter", 14.0).status_code == 200
    assert approve(clean, "f-hole", "diameter", 10.0).status_code == 200
    assert approve(clean, "r-tb", "angle", 0.0).status_code == 200
    stored = file_bytes(clean)
    files = on_disk(clean)
    step = cad(clean, "fits.step")
    assert step.status_code == 200, step.text
    products = read(step.text)["products"]
    boss = next(x for x in products["Boss"] if x["type"] == "cylinder")
    hole = next(x for x in products["Hole"] if x["type"] == "circle")
    assert boss["radius"] == pytest.approx(7.0, abs=1e-12) and hole["radius"] == pytest.approx(5.0, abs=1e-12)
    macro = cad(clean, "fits.FCMacro").text
    assert "radius=7.0," in macro and "radius=5.0" in macro and "radius=7.01" not in macro
    s = cad(clean).json()
    want = ["Boss: nominal diameter 14 mm (measured 14.02 +/- 0.03 mm)",
            "Hole: nominal diameter 10 mm (measured 10.04 +/- 0.03 mm)"]
    for note in want:
        assert note in s["notes"], s["notes"]
        assert note in step.text and note in macro
    rel_notes = [n for n in s["notes"] if n.startswith("Top to bottom")]
    assert len(rel_notes) == 1 and "parallel" in rel_notes[0] and "not applied" in rel_notes[0]
    nom = s["nominal"]
    assert nom["measured_only"] is False and nom["sizes_used"] == 2 and nom["relations_listed"] == 1
    assert "not applied to the geometry" in nom["relations_note"]
    assert [x["id"] for x in nom["sizes"]] == ["f-boss", "f-hole"]
    assert s["warnings"] == [] and s["counts"] == plain_summary["counts"]
    # measured only: exactly the files of before
    assert cad(clean, "fits.step", measured_only="true").content == plain_step
    assert cad(clean, "fits.FCMacro", measured_only="true").content == plain_macro
    m = cad(clean, measured_only="true").json()
    assert m["notes"] == plain_summary["notes"] and m["nominal"]["measured_only"] is True
    assert m["nominal"]["sizes_used"] == 0 and m["step_bytes"] == len(plain_step)
    # nothing on disk changed: the measured shapes stay in features.json, byte for byte
    assert file_bytes(clean) == stored and on_disk(clean) == files
    assert by_id(kf.load(scan_dir(clean))["features"], "f-boss")["shape"]["diameter_mm"] == 14.02


def test_an_approval_marked_check_again_is_used_with_a_warning(clean):
    keep_set(clean)
    assert approve(clean, "f-hole", "diameter", 10.0).status_code == 200
    doc = kf.load(scan_dir(clean))
    shape = by_id(doc["features"], "f-hole")["shape"]
    shape["radius_mm"], shape["diameter_mm"] = 5.2, 10.4  # fitted again: 10 no longer fits
    kf.save(scan_dir(clean), doc)
    s = cad(clean).json()
    assert s["nominal"]["sizes_used"] == 1 and s["nominal"]["sizes"][0]["check_again"] is True
    assert any("Hole" in w and "check again" in w for w in s["warnings"])
    assert "Hole: nominal diameter 10 mm (measured 10.40 +/- 0.03 mm)" in s["notes"]
    hole = next(x for x in read(cad(clean, "fits.step").text)["products"]["Hole"] if x["type"] == "circle")
    assert hole["radius"] == pytest.approx(5.0, abs=1e-12)


def test_a_file_written_before_approvals_loads_and_exports_as_before(clean):
    keep(clean, "box_with_hole_and_boss.step")
    stored = file_bytes(clean)
    s = get(base_url(clean) + "/features").json()
    assert all(f["nominals"] == [] for f in s["features"])
    want_step, want_macro, shapes = engine(clean)
    assert cad(clean, "fits.step").content == want_step.encode("ascii")
    assert cad(clean, "fits.FCMacro").content == want_macro.encode("ascii")
    summary = cad(clean).json()
    assert summary["notes"] == shapes.notes and summary["nominal"]["sizes_used"] == 0
    assert summary["nominal"]["relations_note"] is None
    assert file_bytes(clean) == stored
    assert hashlib.sha256(file_bytes(clean)).hexdigest() == hashlib.sha256(stored).hexdigest()


def test_the_file_refuses_damaged_approvals(tmp_path):
    doc = kf.empty()
    doc["features"] = [record("f-boss", "Boss", "cylinder", cyl_shape(14.02, 0.03), 1, form="boss")]
    good = {"value": "diameter", "nominal": 14.0, "unit": "mm", "word": None, "source": "suggested", "measured": 14.02,
            "sigma": 0.03, "approved": "2026-11-17T12:00:00"}
    doc["features"][0]["nominals"] = [good]
    assert kf.validate(doc)["features"][0]["nominals"] == [good]
    for bad in ({"nominal": -1.0}, {"nominal": float("nan")}, {"value": "angle"}, {"source": "rounded"},
                {"unit": "deg"}, {"measured": None}, {"sigma": -0.1}):
        d = copy.deepcopy(doc)
        d["features"][0]["nominals"] = [{**good, **bad}]
        with pytest.raises(ValueError):
            kf.validate(d)
    d = copy.deepcopy(doc)
    d["features"][0]["nominals"] = [good, {**good, "value": "radius", "nominal": 7.0}]  # one size per fit
    with pytest.raises(ValueError):
        kf.validate(d)
    d = copy.deepcopy(doc)
    d["features"].append(record("f-top", "Top", "plane", plane_shape([0, 0, 1], [0, 0, 0]), 1))
    d["features"][1]["nominals"] = [good]  # a plane has no size
    with pytest.raises(ValueError):
        kf.validate(d)
