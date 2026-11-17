"""ohs.report: the kept features as CSV and as JSON, beside the report (FEATURE_PLAN I3).

The same full example as ``tests/test_report_html.py`` (fits of noisy points kept as
``features.json`` records, two relations, the part's frame). The CSV is read back with the ``csv``
module: one row per feature and per relation, the stated column order, every number as it was
given, and names that a spreadsheet would read as a formula written so that it reads them as text.
The JSON is read back with ``json``.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import math

import pytest

pytest.importorskip("open3d")  # the example's comparison

from test_report_html import FIT_HOSTILE, example  # noqa: E402

from ohs.measure.relate import relate  # noqa: E402


def _rows(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.reader(io.StringIO(text, newline=""))
    rows = list(reader)
    head = rows[0]
    return head, [dict(zip(head, r)) for r in rows[1:]]


def _num(cell: str) -> float | None:
    return None if cell == "" else float(cell)


def test_one_row_per_feature_and_relation_in_the_stated_order():
    from ohs.report import CSV_COLUMNS, features_csv

    data = example()
    doc = data["features"]
    text = features_csv(data)
    head, rows = _rows(text)
    assert head == list(CSV_COLUMNS)
    assert len(set(head)) == len(head)
    assert [r["record"] for r in rows] == ["feature"] * len(doc["features"]) + ["relation"] * len(doc["relations"])
    assert [r["id"] for r in rows] == [f["id"] for f in doc["features"]] + [r["id"] for r in doc["relations"]]
    assert all(len(r) == len(head) for r in rows)


def test_numbers_and_their_plus_minus_parse_back_to_the_same_values():
    from ohs.report import features_csv

    data = example()
    doc = data["features"]
    _, rows = _rows(features_csv(data))
    by_id = {r["id"]: r for r in rows}
    for f in doc["features"]:
        row, s = by_id[f["id"]], f["shape"]
        assert row["name"] == f["name"] and row["kind"] == f["kind"] and row["frame"] == "scan"
        pos = s.get("point") or s.get("center")
        direction = s.get("normal") or s.get("axis")
        assert [_num(row[k]) for k in ("x_mm", "y_mm", "z_mm")] == pos
        assert [_num(row[k]) for k in ("dir_x", "dir_y", "dir_z")] == direction
        if f["kind"] == "plane":
            assert _num(row["rms_mm"]) == s["rms_mm"] and row["diameter_mm"] == ""
            assert _num(row["position_sigma_mm"]) == s["offset_sigma_mm"]
            assert _num(row["direction_sigma_deg"]) == s["normal_sigma_deg"]
        else:
            assert _num(row["diameter_mm"]) == s["diameter_mm"]
            assert _num(row["diameter_sigma_mm"]) == s["diameter_sigma_mm"]
        if f["kind"] == "hole":
            assert row["direction_sigma_deg"] == ""  # a hole's direction has no ±
    boss = by_id["f-boss"]
    assert _num(boss["nominal_diameter_mm"]) == 14.0 and boss["form"] == "boss"
    assert _num(boss["length_mm"]) == 10.0 and _num(boss["arc_deg"]) == 360.0
    top = by_id["f-top"]
    assert (_num(top["length_mm"]), _num(top["width_mm"]), _num(top["area_mm2"])) == (40.0, 30.0, 1121.5)
    hole = by_id["f-hole"]
    assert hole["check_again"] == "yes" and _num(hole["nominal_diameter_mm"]) == 10.5
    assert "small hole" in hole["warnings"] and "Fitted on an earlier mesh" in hole["warnings"]
    feats = {f["id"]: f for f in doc["features"]}
    for r in doc["relations"]:
        row = by_id[r["id"]]
        a, b = feats[r["a"]], feats[r["b"]]
        assert (row["of_a"], row["of_b"]) == (a["name"], b["name"])
        rec = relate({"kind": a["kind"], "shape": a["shape"]}, {"kind": b["kind"], "shape": b["shape"]})
        for q in rec.values:
            col = {"angle": "angle_deg", "distance": "distance_mm", "centre_distance": "centre_distance_mm"}[q.name]
            sig = {"angle": "angle_sigma_deg", "distance": "distance_sigma_mm",
                   "centre_distance": "centre_distance_sigma_mm"}[q.name]
            assert _num(row[col]) == q.value
            assert _num(row[sig]) == q.sigma
    assert by_id["r-ts"]["cad_move"] == "Side face moves to suit Top face"
    assert _num(by_id["r-ts"]["nominal_angle_deg"]) == 90.0
    assert by_id["r-hb"]["cad_move"] == "Listed only"


def test_numbers_in_the_part_frame_when_the_data_says_so():
    from ohs.measure.frame import PartFrame
    from ohs.report import features_csv

    data = example()
    data["numbers_in_part_frame"] = True
    _, rows = _rows(features_csv(data))
    hole = next(r for r in rows if r["id"] == "f-hole")
    assert hole["frame"] == "part"
    # the hole is the frame's origin: its centre reads X = Y = 0 there, and Z its height over the top face
    # it was moved onto (the noise of the two fits)
    assert all(abs(_num(hole[k])) < 1e-9 for k in ("x_mm", "y_mm")) and abs(_num(hole["z_mm"])) < 0.05
    fr = PartFrame.from_dict(data["features"]["frame"])
    top = next(r for r in rows if r["id"] == "f-top")
    want = fr.points_to_part(data["features"]["features"][0]["shape"]["point"])
    assert all(math.isclose(_num(top[k]), float(w), abs_tol=1e-9) for k, w in zip(("x_mm", "y_mm", "z_mm"), want))


@pytest.mark.parametrize("name", ["=1+2", "+cmd|' /C calc'!A0", "-2+3", "@SUM(A1:A2)", "\t=1", "\r=1"])
def test_a_name_that_looks_like_a_formula_is_text(name):
    from ohs.report import features_csv

    data = example()
    data["features"]["features"][0]["name"] = name
    data["features"]["relations"][0]["name"] = name
    _, rows = _rows(features_csv(data))
    for row in (rows[0], next(r for r in rows if r["record"] == "relation" and r["id"] == "r-ts")):
        cell = row["name"]
        assert cell.startswith("'") and cell[1:] == name
    assert next(r for r in rows if r["id"] == "r-ts")["of_a"] == "'" + name
    # numbers, negative ones too, are written as numbers
    assert any(r["x_mm"].startswith("-") for r in rows) or any(r["dir_x"].startswith("-") for r in rows)


def test_the_hostile_name_is_kept_in_the_csv_as_text():
    from ohs.report import features_csv

    _, rows = _rows(features_csv(example()))
    assert next(r for r in rows if r["id"] == "f-boss")["name"] == FIT_HOSTILE


def test_the_json_round_trips():
    from ohs.report import features_document, features_json

    data = example()
    text = features_json(data)
    assert "NaN" not in text and "Infinity" not in text
    back = json.loads(text)
    assert back == features_document(data)
    assert text == features_json(copy.deepcopy(data))  # the same data, the same text
    assert [f["name"] for f in back["features"]] == [f["name"] for f in data["features"]["features"]]
    assert back["features"][2]["name"] == FIT_HOSTILE
    assert back["features"][3]["stale"] is True and back["features"][0]["stale"] is False
    rel = back["relations"][0]
    assert rel["a_name"] == "Top face" and rel["b_name"] == "Side face"
    assert [q["name"] for q in rel["values"]] == ["angle"]
    assert back["frame"]["out_of_date"] is False and back["units"] == {"length": "mm", "angle": "deg"}
    assert back["sections"][0]["length_mm"] == pytest.approx(80.0)
    assert "svg" not in back["sections"][0]
