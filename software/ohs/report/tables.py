"""The kept features as CSV and as JSON, to go with the report (FEATURE_PLAN I3).

**CSV** (:func:`features_csv`): one row per kept fit, then one per kept relation, with the columns
of :data:`CSV_COLUMNS` in that order (the first row names them). A value and its ± are in separate
columns; a column that does not apply to a row is empty. Numbers are written in full (Python's
shortest form that reads back to the same number: ``14.023417``), in millimetres and degrees,
positions and directions in the part's frame when the data asks for it (``frame`` says which).

* ``record``: ``feature`` or ``relation``; ``id``, ``name``; ``kind``: plane, cylinder, hole, or
  for a relation its two kinds ("plane-plane"); ``form``: a cylinder's boss or bore;
* ``of_a``, ``of_b``: a relation's two fits, by name;
* ``frame``: ``part`` or ``scan``, the axes of the position and direction columns;
* ``diameter_mm``, ``diameter_sigma_mm``: a cylinder's or a hole's; ``nominal_diameter_mm`` or
  ``nominal_radius_mm``: the approved value, as approved; ``rms_mm``: how far the points lie from
  the fit;
* ``x_mm``, ``y_mm``, ``z_mm``, ``position_sigma_mm``: a plane's centre of points (± along its
  normal), a cylinder's point on its axis or a hole's centre (± across the axis);
* ``dir_x``, ``dir_y``, ``dir_z``, ``direction_sigma_deg``: a plane's normal, a cylinder's axis, a
  hole's normal (which has no ±);
* ``length_mm``, ``width_mm``, ``area_mm2``, ``arc_deg``: a plane's rectangle and measured area, a
  cylinder's length and how much of its round was scanned;
* ``angle_deg``, ``distance_mm``, ``centre_distance_mm``, each with its ``..._sigma_...`` and its
  ``nominal_...``: a relation's values (``ohs.measure.relate``) and approved values;
* ``cad_move``: what an approved relation does in the CAD files ("Listed only", or "<fit> moves to
  suit <fit>"); ``check_again``: ``yes`` when an approval is marked check again; ``stale``: ``yes``
  when a fit (or one of a relation's) was made on an earlier mesh; ``warnings``: its warnings,
  joined by " | ".

**Safe in a spreadsheet.** A text that starts with ``=``, ``+``, ``-``, ``@``, a tab or a carriage
return would be read as a formula by a spreadsheet; such a text is written with an apostrophe in
front ("'=1+2"), which a spreadsheet shows as text. Numbers, negative ones too, are written as
numbers. Write the text as UTF-8 with a byte-order mark (``encoding="utf-8-sig"``) so that a
spreadsheet reads "±" and "Ø" right; the text returned here has none.

**JSON** (:func:`features_json`, :func:`features_document`): the kept features as given, tidied
(plain JSON numbers, no NaN, no bytes), with what the report worked out beside them: each fit's
``stale``, each relation's two names, values, notes and ``cad_move``, the frame's state, and each
section's numbers (not its drawing). Positions in it stay in the scan's frame, as kept; the frame
is there to turn them.
"""
from __future__ import annotations

import csv
import io
import json

from ohs.report.data import FORMAT_VERSION, name_of, prepare, tidy
from ohs.report.numbers import is_number

__all__ = ["CSV_COLUMNS", "features_csv", "features_document", "features_json", "FORMULA_STARTS"]

CSV_COLUMNS = (
    "record", "id", "name", "kind", "form", "of_a", "of_b", "frame",
    "diameter_mm", "diameter_sigma_mm", "nominal_diameter_mm", "nominal_radius_mm", "rms_mm",
    "x_mm", "y_mm", "z_mm", "position_sigma_mm", "dir_x", "dir_y", "dir_z", "direction_sigma_deg",
    "length_mm", "width_mm", "area_mm2", "arc_deg",
    "angle_deg", "angle_sigma_deg", "nominal_angle_deg",
    "distance_mm", "distance_sigma_mm", "nominal_distance_mm",
    "centre_distance_mm", "centre_distance_sigma_mm", "nominal_centre_distance_mm",
    "cad_move", "check_again", "stale", "warnings",
)
#: a text cell starting with one of these is written with an apostrophe in front (module docstring)
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

_VALUE_COLUMNS = {"angle": ("angle_deg", "angle_sigma_deg", "nominal_angle_deg"),
                  "distance": ("distance_mm", "distance_sigma_mm", "nominal_distance_mm"),
                  "centre_distance": ("centre_distance_mm", "centre_distance_sigma_mm", "nominal_centre_distance_mm")}


def _cell(x) -> str:
    """One CSV cell: a number in full, yes for true, empty for nothing, text made safe."""
    if x is None or x is False:
        return ""
    if x is True:
        return "yes"
    if is_number(x):
        return repr(int(x)) if isinstance(x, int) else repr(float(x))
    s = str(x)
    return "'" + s if s.startswith(FORMULA_STARTS) else s


def _feature_row(v: dict) -> dict:
    row = {"record": "feature", "id": v["id"], "name": v["name"], "kind": v["kind"], "form": v.get("form"),
           "frame": v["frame"], "rms_mm": v.get("rms_mm"), "position_sigma_mm": v.get("position_sigma"),
           "direction_sigma_deg": v.get("direction_sigma"), "length_mm": v.get("length_mm"),
           "width_mm": v.get("width_mm"), "area_mm2": v.get("area_mm2"), "arc_deg": v.get("arc_deg"),
           "stale": v["stale"], "warnings": " | ".join(v["notes"])}
    if v["shape"].get("kind") in ("cylinder", "circle"):
        row["diameter_mm"], row["diameter_sigma_mm"] = v.get("diameter_mm"), v.get("diameter_sigma_mm")
    for col, val in zip(("x_mm", "y_mm", "z_mm"), v.get("position") or ()):
        row[col] = val
    for col, val in zip(("dir_x", "dir_y", "dir_z"), v.get("direction") or ()):
        row[col] = val
    for n in v["nominals"]:
        if n.get("value") in ("diameter", "radius"):
            row[f"nominal_{n['value']}_mm"] = n.get("nominal")
        row["check_again"] = row.get("check_again") or bool(n.get("check_again"))
    return row


def _relation_row(r: dict, kinds: dict) -> dict:
    row = {"record": "relation", "id": r["id"], "name": r["name"], "of_a": r["a_name"], "of_b": r["b_name"],
           "kind": f"{kinds.get(r['a'], '?')}-{kinds.get(r['b'], '?')}", "cad_move": r["cad_move"],
           "stale": r["stale"], "warnings": " | ".join(r["notes"] + r["warnings"])}
    for q in r["values"]:
        cols = _VALUE_COLUMNS.get(q.get("name"))
        if cols:
            row[cols[0]], row[cols[1]] = q.get("value"), q.get("sigma")
    for n in r["nominals"]:
        cols = _VALUE_COLUMNS.get(n.get("value"))
        if cols:
            row[cols[2]] = n.get("nominal")
        row["check_again"] = row.get("check_again") or bool(n.get("check_again"))
    return row


def features_csv(data: dict) -> str:
    """The kept fits and relations of ``data["features"]`` as CSV text (module docstring): a head
    row of :data:`CSV_COLUMNS`, then one row per fit and one per relation, lines ending in CRLF."""
    prep = prepare(data)
    kinds = {v["id"]: v["kind"] for v in prep.features}
    rows = [_feature_row(v) for v in prep.features] + [_relation_row(r, kinds) for r in prep.relations]
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for row in rows:
        w.writerow([_cell(row.get(c)) for c in CSV_COLUMNS])
    return buf.getvalue()


def features_document(data: dict) -> dict:
    """The kept features as one plain JSON object (module docstring); what :func:`features_json`
    writes."""
    prep = prepare(data)
    doc = data.get("features") if isinstance(data.get("features"), dict) else {}
    stale = {v["id"]: v["stale"] for v in prep.features}
    feats = [{**tidy(f), "stale": stale.get(f.get("id"), False)} for f in doc.get("features") or []
             if isinstance(f, dict)]
    meas = {m["id"]: m["stale"] for m in prep.measurements}
    measurements = [{**tidy(m), "stale": meas.get(m.get("id"), False)} for m in doc.get("measurements") or []
                    if isinstance(m, dict)]
    views = {r["id"]: r for r in prep.relations}
    relations = []
    for r in doc.get("relations") or []:
        if not isinstance(r, dict):
            continue
        v = views.get(r.get("id"), {})
        out = {k: x for k, x in tidy(r).items() if k != "relation"}
        out.update({"a_name": v.get("a_name"), "b_name": v.get("b_name"), "values": tidy(v.get("values", [])),
                    "notes": list(v.get("notes", [])), "cad_move": v.get("cad_move"), "stale": v.get("stale", False),
                    "error": v.get("error")})
        relations.append(out)
    sections = [{k: tidy(x) for k, x in s.items() if k != "svg"} for s in data.get("sections") or []
                if isinstance(s, dict)]
    return {"format": "OpenHandScan kept features", "format_version": FORMAT_VERSION,
            "project": name_of(data.get("project")), "scan": name_of(data.get("scan")),
            "date": tidy(data.get("date")), "app_version": tidy(data.get("app_version")),
            "units": {"length": "mm", "angle": "deg"},
            "plus_minus": "1σ: the spread from scan to scan, not the accuracy",
            "mesh_version": tidy(doc.get("mesh_version")),
            "frame": tidy(prep.frame), "features": feats, "measurements": measurements, "relations": relations,
            "sections": sections}


def features_json(data: dict) -> str:
    """:func:`features_document` as JSON text (UTF-8, indented, the keys in a fixed order)."""
    return json.dumps(features_document(data), indent=1, ensure_ascii=False, allow_nan=False) + "\n"
