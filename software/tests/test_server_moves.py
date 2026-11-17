"""An approved relation that moves a shape in the CAD export, and offers that only a known bias allows
(the owner's two decisions after FEATURE_PLAN.md C3; APP_API.md "Nominal values", "For CAD").

* A relation's move is chosen when approving (``move`` in the body) or afterwards (``PUT
  .../features/{id}/move``): "listed" (the default), "a" or "b". It is kept on the relation in
  ``features.json``; the measured fits never change.
* The STEP file read back shows the moved shape turned and the other one untouched; the notes and
  the summary say how far it was turned and slid. "Listed only" gives exactly the files of before,
  and ``measured_only`` the files without approvals. A move that would spoil an earlier one is not
  applied: it stays listed, with a warning.
* ``GET .../nominal`` offers what only a known bias allows only with ``with_bias=true``; an approval
  made that way keeps its rule, so it is not marked "check again" while the choice is off.

Uses the scan and the kept set of ``test_server_nominal.py`` (two planes 0.05° apart, a boss, a
hole and their relation), written straight into ``features.json``.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

import ohs.measure.features as kf  # noqa: E402
from ohs.cad.nominal import suggest_fit  # noqa: E402
from step_reader import read  # noqa: E402
from test_server_cad import on_disk, scan_dir, version  # noqa: E402
from test_server_nominal import (TILT, by_id, clean, cyl_shape, file_bytes, keep_set, nominal_url, plane_extent,  # noqa: E402,F401
                                 plane_shape, record, tracked)
from test_server_process_studio import T, base_url, env, err, get, post  # noqa: E402,F401


def approve(e: dict, item: str, value: str, nominal: float, source: str = "suggested", **more):
    return post(nominal_url(e, item), {"value": value, "nominal": nominal, "source": source, **more})


def move(e: dict, item: str, choice: str):
    return requests.put(base_url(e) + f"/features/{item}/move", json={"move": choice}, timeout=T)


def cad(e: dict, name: str = "", **query):
    return requests.get(base_url(e) + "/features/cad" + (f"/{name}" if name else ""), params=query or None, timeout=T)


def plane_of(step_text: str, name: str) -> dict:
    return next(x for x in read(step_text)["products"][name] if x["type"] == "plane")


def measured(doc: dict) -> dict:
    """``features.json`` without what an engineer chose (approvals, moves): what was measured."""
    out = {k: v for k, v in doc.items()}
    out["features"] = [{k: v for k, v in f.items() if k != "nominals"} for f in doc["features"]]
    out["relations"] = [{k: v for k, v in r.items() if k not in ("nominals", "move")} for r in doc["relations"]]
    return out


def add_plane(e: dict, fid: str, name: str, deg: float, z: float) -> None:
    """One more kept plane, its normal turned ``deg`` about the y axis, through (0, 0, z)."""
    doc = kf.load(scan_dir(e))
    t = math.radians(deg)
    n = [math.sin(t), 0.0, math.cos(t)]
    doc["features"].append(record(fid, name, "plane", plane_shape(n, [0.0, 0.0, z]), version(e),
                                  extent=plane_extent([0.0, 0.0, z], [math.cos(t), 0.0, -math.sin(t)])))
    kf.save(scan_dir(e), doc)


# --------------------------------------------------------------------------- a relation moves a shape
def test_a_relation_moves_the_shape_chosen_and_listed_only_gives_the_files_of_before(clean):  # noqa: F811 (the fixture imported above)
    keep_set(clean)
    before = kf.load(scan_dir(clean))
    plain_step, plain_macro = cad(clean, "fits.step").content, cad(clean, "fits.FCMacro").content
    # approved, listed only (the default): today's files
    r = approve(clean, "r-tb", "angle", 0.0)
    assert r.status_code == 200, r.text
    listed_step, listed_macro = cad(clean, "fits.step").content, cad(clean, "fits.FCMacro").content
    rel = by_id(get(base_url(clean) + "/features").json()["relations"], "r-tb")
    assert "move" not in rel
    s = cad(clean).json()
    assert s["nominal"]["moves"] == [] and s["nominal"]["relations_listed"] == 1
    assert [x["move"] for x in s["nominal"]["relations"]] == ["listed"]
    # move the second shape (Bottom) to suit the first: chosen afterwards
    r = move(clean, "r-tb", "b")
    assert r.status_code == 200, r.text
    assert r.json()["move"]["shape"] == "b" and r.json()["nominals"][0]["word"] == "parallel"
    assert kf.load(scan_dir(clean))["relations"][0]["move"]["shape"] == "b"
    stored = file_bytes(clean)
    files = on_disk(clean)
    step = cad(clean, "fits.step").text
    assert file_bytes(clean) == stored and on_disk(clean) == files  # making the files wrote nothing
    top, bottom = plane_of(step, "Top"), plane_of(step, "Bottom")
    assert np.allclose(top["normal"], [0.0, 0.0, 1.0], atol=1e-12) and np.allclose(top["point"], [0.0, 0.0, 10.0], atol=1e-12)
    assert np.allclose(bottom["normal"], [0.0, 0.0, 1.0], atol=1e-12)  # turned parallel to Top ...
    assert np.allclose(bottom["point"], [0.0, 0.0, -0.02], atol=1e-12)  # ... about its own point
    s = cad(clean).json()
    nom = s["nominal"]
    (m,) = nom["moves"]
    assert m["id"] == "r-tb" and m["applied"] is True and m["moved"] == "Bottom" and m["to_suit"] == "Top"
    assert m["turn_deg"] == pytest.approx(0.05, abs=1e-9) and m["slide_mm"] == pytest.approx(0.0, abs=1e-9)
    assert nom["relations_applied"] == 1 and nom["relations_listed"] == 0 and nom["relations_note"] is None
    assert [x["move"] for x in nom["relations"]] == ["b"]
    notes = [n for n in s["notes"] if n.startswith("Top to bottom")]
    assert any("applied" in n and "parallel" in n for n in notes), notes
    assert any("Bottom turned 0.0500 deg about its own point and slid 0.0000 mm" in n for n in notes), notes
    assert all(n in step for n in notes) and all(n in cad(clean, "fits.FCMacro").text for n in notes)
    assert s["warnings"] == []
    # the other one moves instead: Top turned to Bottom's measured direction, Bottom as measured
    assert move(clean, "r-tb", "a").status_code == 200
    step = cad(clean, "fits.step").text
    top, bottom = plane_of(step, "Top"), plane_of(step, "Bottom")
    n = [math.sin(TILT), 0.0, math.cos(TILT)]
    assert np.allclose(top["normal"], n, atol=1e-12) and np.allclose(bottom["normal"], n, atol=1e-12)
    # back to listed only: exactly the files of before; measured only: the files without approvals
    assert move(clean, "r-tb", "b").status_code == 200
    assert cad(clean, "fits.step", measured_only="true").content == plain_step
    assert cad(clean, "fits.FCMacro", measured_only="true").content == plain_macro
    assert move(clean, "r-tb", "listed").status_code == 200
    assert "move" not in kf.load(scan_dir(clean))["relations"][0]
    assert cad(clean, "fits.step").content == listed_step and cad(clean, "fits.FCMacro").content == listed_macro
    # nothing measured changed, and reading the files wrote nothing
    assert measured(kf.load(scan_dir(clean))) == measured(before)
    assert on_disk(clean).keys() == files.keys()


def test_a_move_can_be_chosen_when_approving_and_is_refused_for_a_fit(clean):  # noqa: F811 (the fixture imported above)
    keep_set(clean)
    r = approve(clean, "r-tb", "angle", 0.0, move="b")
    assert r.status_code == 200, r.text
    rel = by_id(get(base_url(clean) + "/features").json()["relations"], "r-tb")
    assert rel["move"]["shape"] == "b" and rel["move"]["order"] == 1
    bottom = plane_of(cad(clean, "fits.step").text, "Bottom")
    assert np.allclose(bottom["normal"], [0.0, 0.0, 1.0], atol=1e-12)
    # a fit has no move; a move must be listed, a or b; unknown ids are 404
    err(approve(clean, "f-boss", "diameter", 14.0, move="b"), 422, "relation")
    err(move(clean, "f-boss", "b"), 422, "relation")
    err(move(clean, "r-tb", "c"), 422)
    err(move(clean, "r-nope", "a"), 404)
    # choosing the same again keeps its place in the order; another choice takes a new one
    assert move(clean, "r-tb", "b").json()["move"]["order"] == 1
    assert move(clean, "r-tb", "a").json()["move"]["order"] == 2


def test_a_move_that_would_spoil_an_earlier_one_is_listed_with_a_warning(clean):  # noqa: F811 (the fixture imported above)
    keep_set(clean)
    add_plane(clean, "f-mid", "Mid", -0.03, 5.0)
    r = post(base_url(clean) + "/features/relations", {"a": "f-bottom", "b": "f-mid", "name": "Bottom to mid"})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert approve(clean, "r-tb", "angle", 0.0, move="b").status_code == 200  # Bottom turned to suit Top
    assert approve(clean, rid, "angle", 0.0, "entered", move="a").status_code == 200  # Bottom again: refused
    s = cad(clean).json()
    first, second = s["nominal"]["moves"]
    assert first["applied"] is True and second["applied"] is False
    assert "already moved" in second["reason"] and "Top to bottom" in second["reason"]
    assert any("Bottom to mid" in w and "already moved" in w for w in s["warnings"]), s["warnings"]
    assert any(n.startswith("Bottom to mid") and "not applied to the geometry" in n for n in s["notes"])
    assert s["nominal"]["relations_applied"] == 1 and s["nominal"]["relations_listed"] == 1
    # Mid is exported as measured; Bottom as the first move made it
    step = cad(clean, "fits.step").text
    t = math.radians(-0.03)
    assert np.allclose(plane_of(step, "Mid")["normal"], [math.sin(t), 0.0, math.cos(t)], atol=1e-12)
    assert np.allclose(plane_of(step, "Bottom")["normal"], [0.0, 0.0, 1.0], atol=1e-12)
    # moving Mid instead is fine: it suits Bottom as exported, and both hold
    assert move(clean, rid, "b").status_code == 200
    s = cad(clean).json()
    assert [m["applied"] for m in s["nominal"]["moves"]] == [True, True] and s["warnings"] == []
    step = cad(clean, "fits.step").text
    assert np.allclose(plane_of(step, "Mid")["normal"], [0.0, 0.0, 1.0], atol=1e-12)


def test_a_move_whose_approval_is_marked_check_again_is_applied_with_a_warning(clean):  # noqa: F811 (the fixture imported above)
    keep_set(clean)
    assert approve(clean, "r-tb", "angle", 0.0, move="b").status_code == 200
    doc = kf.load(scan_dir(clean))
    t = math.radians(1.0)  # Bottom fitted again a degree off: parallel no longer fits its ±
    by_id(doc["features"], "f-bottom")["shape"]["normal"] = [math.sin(t), 0.0, math.cos(t)]
    kf.save(scan_dir(clean), doc)
    s = cad(clean).json()
    (m,) = s["nominal"]["moves"]
    assert m["applied"] is True and m["check_again"] is True and m["turn_deg"] == pytest.approx(1.0, abs=1e-9)
    assert any("Top to bottom" in w and "check again" in w for w in s["warnings"]), s["warnings"]
    assert np.allclose(plane_of(cad(clean, "fits.step").text, "Bottom")["normal"], [0.0, 0.0, 1.0], atol=1e-12)


def test_the_file_refuses_a_damaged_move(tmp_path):
    doc = kf.empty()
    doc["features"] = [record("f-a", "A", "plane", plane_shape([0, 0, 1], [0, 0, 0]), 1),
                       record("f-b", "B", "plane", plane_shape([0, 0, 1], [0, 0, 5]), 1)]
    doc["relations"] = [{"id": "r-1", "name": "R", "a": "f-a", "b": "f-b", "created": "2026-11-17T12:00:00",
                         "move": {"shape": "b", "order": 1, "chosen": "2026-11-17T12:00:00"}}]
    assert kf.validate(doc)["relations"][0]["move"]["shape"] == "b"
    for bad in ({"shape": "c", "order": 1}, {"shape": "a", "order": 0}, {"shape": "a", "order": 1.5}, "b"):
        d = {**doc, "relations": [{**doc["relations"][0], "move": bad}]}
        with pytest.raises(ValueError):
            kf.validate(d)


# --------------------------------------------------------------------------- offers a bias alone allows
def small_boss(e: dict, fid: str = "f-small", name: str = "Small boss", d: float = 13.66) -> dict:
    """A boss that reads 0.34 mm small: 14 is far outside its ± and only the known bias allows it."""
    doc = kf.load(scan_dir(e))
    doc["features"].append(record(fid, name, "cylinder", cyl_shape(d, 0.01), version(e), form="boss"))
    return kf.save(scan_dir(e), doc)


def test_the_offers_route_with_and_without_the_bias_choice(clean):  # noqa: F811 (the fixture imported above)
    keep_set(clean)
    doc = small_boss(clean)
    feat = by_id(doc["features"], "f-small")
    off = get(nominal_url(clean, "f-small")).json()
    assert off["with_bias"] is False and off["offers"] == [o.to_dict() for o in suggest_fit(feat, poses="tracked")]
    assert off["offers"][0]["suggestions"] == [] and off["offers"][0]["bias_only"] == [14.0]
    on = get(nominal_url(clean, "f-small") + "?with_bias=true").json()
    assert on["with_bias"] is True
    assert on["offers"] == [o.to_dict() for o in suggest_fit(feat, poses="tracked", with_bias=True)]
    (s,) = on["offers"][0]["suggestions"]
    assert s["nominal"] == 14.0 and s["allowed_by"] == "bias"
    # a suggested value must be offered under the rule asked for
    err(approve(clean, "f-small", "diameter", 14.0), 422, "offered")
    r = approve(clean, "f-small", "diameter", 14.0, with_bias=True)
    assert r.status_code == 200, r.text
    assert r.json()["with_bias"] is True and r.json()["nominals"][0]["with_bias"] is True
    assert by_id(kf.load(scan_dir(clean))["features"], "f-small")["nominals"][0]["with_bias"] is True


def test_an_approval_made_with_the_bias_allowed_keeps_its_rule(clean):  # noqa: F811 (the fixture imported above)
    keep_set(clean)
    small_boss(clean)
    small_boss(clean, "f-other", "Other boss")
    assert approve(clean, "f-small", "diameter", 14.0, with_bias=True).status_code == 200
    assert approve(clean, "f-other", "diameter", 14.0, "entered").status_code == 200  # by the ± alone
    assert "with_bias" not in by_id(kf.load(scan_dir(clean))["features"], "f-other")["nominals"][0]
    # measured again a little differently: still within the bias, never within the ±
    doc = kf.load(scan_dir(clean))
    for fid in ("f-small", "f-other"):
        shape = by_id(doc["features"], fid)["shape"]
        shape["radius_mm"], shape["diameter_mm"] = 13.67 / 2, 13.67
    kf.save(scan_dir(clean), doc)
    s = get(base_url(clean) + "/features").json()  # the choice is off here
    assert by_id(s["features"], "f-small")["nominals"][0]["check_again"] is False
    other = by_id(s["features"], "f-other")["nominals"][0]
    assert other["check_again"] is True and "only a known bias" in other["check_reason"]
    assert get(nominal_url(clean, "f-small")).json()["nominals"][0]["check_again"] is False
