"""What a scan's report is built from: one plain structure, every part optional.

The report (:func:`ohs.report.build_html`) and the kept features as CSV and JSON
(:mod:`ohs.report.tables`) are built from a dict of plain data, each part as the module that makes
it already gives it. Nothing here reads a file, the clock or the server: whoever collects the data
(the server, later) hands it over, and the same data always gives the same report. A part that is
missing, None or empty is left out of the report, not shown empty.

**The input** (every key optional)::

    {"project": {"name": "Door bracket"},          # or a plain string
     "scan": {"name": "Scan 3, front"},            # or a plain string
     "date": "2026-11-17",                         # the report's date, as text (not read from the clock)
     "app_version": "0.3.0",                       # ohs.__version__, given
     "process": {...},       # the scan's processing report: result/report.json (ohs.app.process), or the
                             # summary the app keeps of it (ohs.server.projects.report_summary): mode,
                             # quality, source, scene, frames, failures, imu, markers, poses, mesh, lasers,
                             # wall_time_s; other keys are ignored
     "calibration": {...},   # the setup state's calibration part (ohs.server.setup, SetupManager.state()
                             # ["calibration"]): "parts" {"stereo" | "lasers" | "imu": {"when", "summary"}
                             # or None}, "modified", "baseline_mm", "lasers_missing"; and beside it
                             # "last_check" ({"when", "ok", "verdict"}, the setup state's) and "health"
                             # ({"state": "valid" | "check" | "recalibrate", "part", "reason", and optional
                             # "not_judged", "note", "provisional", "source"}: the scan's own status,
                             # report.json's health_status, H3/H4; without it the report says "Not assessed")
     "check": {...},         # ohs.mesh.check.weak_areas(...): the Check answer at one tolerance (the
                             # areas' "faces" are not used)
     "features": {...},      # the scan's kept features: features.json (ohs.measure.features) as kept, or
                             # as GET .../features shows it (with "mesh_version", each fit's "stale",
                             # each relation's "relation" and "stale", each approval's "check_again" and
                             # "check_reason", the frame's "out_of_date"); what is missing is worked out
                             # here (relate, frame_state); kept "sections" there are not drawn (below)
     "numbers_in_part_frame": false,   # true: fits' positions and directions in the part's frame
     "cad_moves": [...],     # optional: ohs.cad.moves.Move.to_dict() of each relation with a move, to say
                             # whether the move could be made in the CAD files
     "sections": [...],      # one entry per kept section, cut on the current mesh: section_entry(...)
     "comparison": {...},    # ohs.inspect.reference.Comparison.to_dict() (the regions' faces are not used)
     "gap_flush": {...},     # ohs.inspect.gapflush.GapFlush.to_dict(), with an optional "name"
     "pictures": [{"title": "Front", "data": b"...", "caption": "..."}]}   # PNG, JPEG, GIF or WebP bytes

**Warnings that were not resolved** (roadmap, principle 4) are collected from every part by
:func:`prepare`: a fit's own warnings, a fit made on an earlier mesh, an approved value marked
"check again", the part's frame's warnings and its being out of date, a relation whose fits are
out of date or that cannot be related, a move that could not be made, a section's warnings, a
missing laser calibration, a board check that did not pass, a calibration health other than
Valid, the reference's and the alignment's warnings, an alignment not accepted, units not
settled, and stations along a seam whose flush is not trusted. Each one is shown where it belongs
and again in the list near the top of the report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ohs.report.numbers import is_number

__all__ = ["FORMAT_VERSION", "PARTS", "ReportWarning", "Prepared", "prepare", "section_entry", "name_of", "tidy"]

FORMAT_VERSION = 1  # of the input structure and of the JSON (ohs.report.tables)

#: the report's parts in order: (anchor, heading). "head" is the page's head, not listed here.
PARTS = (
    ("warnings", "Warnings that were not resolved"),
    ("meaning", "What the numbers mean"),
    ("made", "How the scan was made"),
    ("calibration", "Calibration"),
    ("check", "Does the scan meet the tolerance?"),
    ("frame", "The part's coordinate system"),
    ("pictures", "Views of the model"),
    ("features", "Measured features"),
    ("relations", "Relations between fits"),
    ("sections", "Sections"),
    ("comparison", "Compared with a reference model"),
    ("flush", "Flush along a seam"),
)
HEADING = dict(PARTS)

KIND_WORDS = {"plane": "Plane", "cylinder": "Cylinder", "hole": "Hole"}
ROLE_WORDS = {"primary": "Primary", "secondary": "Secondary", "origin": "Origin"}
SHAPE_WORDS = {"plane": "plane", "cylinder": "cylinder", "circle": "hole", "hole": "hole", "point": "point"}


@dataclass
class ReportWarning:
    """One warning that was not resolved: the part it belongs to (its anchor), the thing it is
    about (a fit's name, or None) and the words."""

    part: str
    item: str | None
    text: str


@dataclass
class Prepared:
    """The input made ready to show: the kept features worked out once for the HTML, the CSV and
    the JSON, and every warning collected."""

    data: dict
    features: list[dict] = field(default_factory=list)  # one view per kept fit (see _feature_view)
    measurements: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    frame: dict | None = None  # the frame as kept, with its state (readable, out_of_date, reasons)
    part_frame: object | None = None  # its PartFrame when it can be read
    in_part_frame: bool = False  # positions and directions are shown in the part's frame
    part_frame_unreadable: bool = False  # they were asked for in the part's frame, which cannot be read
    warnings: list[ReportWarning] = field(default_factory=list)

    def warn(self, part: str, item: str | None, text: str) -> None:
        self.warnings.append(ReportWarning(part, item, str(text)))


# --------------------------------------------------------------------------- small helpers
def name_of(x) -> str | None:
    """A project's or a scan's name: a plain string, or ``{"name": ...}``; None when not given."""
    if isinstance(x, dict):
        x = x.get("name")
    if x is None:
        return None
    s = str(x).strip()
    return s or None


def tidy(x):
    """``x`` as plain JSON: numpy arrays and numbers made lists and floats, tuples lists, numbers
    that are not finite None, bytes left out (None). Keys become text; their order is kept."""
    if isinstance(x, dict):
        return {str(k): tidy(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, np.ndarray)):
        return [tidy(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(float(x)) else None
    if isinstance(x, (bytes, bytearray)):
        return None
    return x


def _list(x) -> list:
    return list(x) if isinstance(x, (list, tuple)) else []


def _text_list(x) -> list[str]:
    return [str(w) for w in _list(x) if w is not None and str(w).strip()]


# --------------------------------------------------------------------------- sections
def section_entry(section, *, name: str, warnings=(), title: str | None = None) -> dict:
    """A kept section, cut on the current mesh, as the report shows it: its name, the plane's
    description, the drawing (:func:`ohs.cad.svg.svg_text`, 1:1 in mm, titled with the name:
    the writer does not wrap a title, and the report shows the description above the drawing) and its numbers: the lines' total length, how many there are and how many
    are closed, the size of the drawing, how much of it runs over filled (not measured) faces, and
    each line's length and enclosed area. ``section`` is an :class:`ohs.mesh.section.Section`;
    ``warnings`` are the section's own (the mesh changed since it was kept, ...)."""
    from ohs.cad.svg import svg_text

    description = str(section.plane.description)
    name = str(name)
    lines = []
    not_measured = 0.0
    for ln in section.lines:
        P = np.vstack([ln.points, ln.points[:1]]) if ln.closed else ln.points
        seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
        filled = float(seg[np.asarray(ln.filled, dtype=bool)[: len(seg)]].sum()) if len(seg) else 0.0
        not_measured += filled
        lines.append({"closed": bool(ln.closed), "length_mm": float(ln.length),
                      "area_mm2": None if ln.area is None else abs(float(ln.area)), "not_measured_mm": filled})
    bb = section.bbox
    return {"name": name, "description": description, "in_part_frame": bool(section.plane.in_part_frame),
            "svg": svg_text(section, title=title if title is not None else name),
            "length_mm": float(section.length), "n_lines": len(lines), "n_closed": sum(ln["closed"] for ln in lines),
            "n_points": int(section.n_points),
            "size_mm": None if bb is None else [float(bb[2] - bb[0]), float(bb[3] - bb[1])],
            "not_measured_mm": not_measured, "lines": lines, "warnings": [str(w) for w in warnings]}


# --------------------------------------------------------------------------- the kept features
def _shape_point(shape: dict):
    return shape.get("point") if shape.get("kind") != "circle" else shape.get("center")


def _shape_direction(shape: dict):
    return shape.get("axis") if shape.get("kind") == "cylinder" else shape.get("normal")


def _feature_view(f: dict, current: int | None, pf, prep: Prepared) -> dict:
    """A kept fit made ready to show: its numbers (in the part's frame when asked), its size, extent,
    approvals and notes. Adds its warnings to ``prep``."""
    shape = f.get("shape") if isinstance(f.get("shape"), dict) else {}
    kind = f.get("kind") or {"circle": "hole"}.get(shape.get("kind"), shape.get("kind"))
    name = str(f.get("name") or f.get("id") or "Fit")
    shown = shape
    if pf is not None:
        try:
            shown = pf.fit_to_part(shape)
        except (ValueError, TypeError, KeyError):
            shown = shape
    view = {"id": f.get("id"), "name": name, "kind": kind, "form": f.get("form"), "shape": shape,
            "frame": "part" if pf is not None else "scan",
            "position": _shape_point(shown), "direction": _shape_direction(shown),
            "position_sigma": shape.get({"plane": "offset_sigma_mm", "cylinder": "point_sigma_mm",
                                         "circle": "center_sigma_mm"}.get(shape.get("kind"), "")),
            "direction_sigma": shape.get({"plane": "normal_sigma_deg", "cylinder": "axis_sigma_deg"}.get(
                shape.get("kind"), "")),
            "rms_mm": shape.get("rms_mm"), "diameter_mm": shape.get("diameter_mm"),
            "diameter_sigma_mm": shape.get("diameter_sigma_mm"), "radius_mm": shape.get("radius_mm"),
            "radius_sigma_mm": shape.get("radius_sigma_mm"),
            "length_mm": None, "width_mm": None, "area_mm2": None, "arc_deg": None, "nominals": [], "notes": []}
    ext = f.get("extent") if isinstance(f.get("extent"), dict) else None
    if ext is not None and ext.get("kind") == "plane":
        half = (ext.get("rectangle") or {}).get("half_sizes_mm") or []
        if len(half) == 2 and all(is_number(h) for h in half):
            view["length_mm"], view["width_mm"] = 2.0 * float(half[0]), 2.0 * float(half[1])
        view["area_mm2"] = ext.get("area_mm2") if is_number(ext.get("area_mm2")) else None
    elif ext is not None and ext.get("kind") == "cylinder":
        view["length_mm"] = ext.get("length_mm") if is_number(ext.get("length_mm")) else None
        view["arc_deg"] = ext.get("arc_deg") if is_number(ext.get("arc_deg")) else None
    notes = view["notes"]
    for w in _text_list(f.get("warnings")):
        notes.append(w)
    stale = f.get("stale")
    if stale is None:
        stale = current is not None and f.get("mesh_version") != current
    view["stale"] = bool(stale)
    if stale:
        was = f.get("mesh_version")
        notes.append("Fitted on an earlier mesh" + (f" (version {was}; the mesh is now version {current})"
                                                     if was is not None and current is not None else "")
                     + ": fit it again to measure the current mesh.")
    for n in _list(f.get("nominals")):
        if not isinstance(n, dict):
            continue
        view["nominals"].append(n)
        if n.get("check_again"):
            notes.append(str(n.get("check_reason") or f"check again: the approved {n.get('value')} no longer fits "
                                                      "the measurement"))
    for w in notes:
        prep.warn("features", name, w)
    return view


def _relation_view(r: dict, by_id: dict, current: int | None, moves: dict, prep: Prepared) -> dict:
    """A kept relation made ready to show: its two fits' names, its values (``relate`` when they
    are not given), its approvals and its move in the CAD files. Adds its warnings to ``prep``."""
    from ohs.measure.relate import relate

    name = str(r.get("name") or r.get("id") or "Relation")
    a, b = by_id.get(r.get("a")), by_id.get(r.get("b"))
    a_name = str(a.get("name")) if a else str(r.get("a"))
    b_name = str(b.get("name")) if b else str(r.get("b"))
    rel, error = r.get("relation"), r.get("error")
    if rel is None and error is None:
        if a is None or b is None:
            error = "one of its two fits is no longer kept"
        else:
            try:
                rel = relate({"kind": a.get("kind"), "shape": a.get("shape")},
                             {"kind": b.get("kind"), "shape": b.get("shape")}).to_dict()
            except (ValueError, TypeError, KeyError) as e:
                error = f"these two fits cannot be related: {e}"
    rel = rel if isinstance(rel, dict) else {}
    stale = r.get("stale")
    if stale is None:
        stale = any(f is None or (current is not None and f.get("mesh_version") != current) for f in (a, b))
    move = r.get("move") if isinstance(r.get("move"), dict) else None
    if move is not None and move.get("shape") in ("a", "b"):
        moved, fixed = (a_name, b_name) if move["shape"] == "a" else (b_name, a_name)
        cad = f"{moved} moves to suit {fixed}"
    else:
        cad = "Listed only"
    view = {"id": r.get("id"), "name": name, "a": r.get("a"), "b": r.get("b"), "a_name": a_name, "b_name": b_name,
            "values": [q for q in _list(rel.get("values")) if isinstance(q, dict)],
            "notes": _text_list(rel.get("notes")), "nominals": [n for n in _list(r.get("nominals")) if isinstance(n, dict)],
            "move": move, "cad_move": cad, "cad_result": None, "stale": bool(stale), "error": error, "warnings": []}
    warns = view["warnings"]
    if error:
        warns.append(str(error))
    if stale:
        warns.append("One of its two fits was made on an earlier mesh: its values are those of the fits as kept.")
    for n in view["nominals"]:
        if n.get("check_again"):
            warns.append(str(n.get("check_reason") or f"check again: the approved {n.get('value')} no longer fits "
                                                      "the measurement"))
    done = moves.get(r.get("id"))
    if move is not None and isinstance(done, dict):
        if done.get("applied"):
            view["cad_result"] = str(done.get("sentence") or "")
        else:
            warns.append(f"Not moved in the CAD files: {done.get('reason') or 'the move could not be made'}")
        warns.extend(_text_list(done.get("warnings")))
    for w in warns:
        prep.warn("relations", name, w)
    return view


def _frame(doc: dict, prep: Prepared) -> None:
    """The kept part's frame with its state, and its PartFrame when it can be read."""
    from ohs.measure import features as kf
    from ohs.measure.frame import PartFrame

    frame = doc.get("frame")
    if not isinstance(frame, dict):
        return
    state = {k: frame[k] for k in ("readable", "out_of_date", "out_of_date_reasons") if k in frame}
    if len(state) < 3:
        state = kf.frame_state(frame, [f for f in _list(doc.get("features")) if isinstance(f, dict)]) or {}
    pf = None
    if state.get("readable", True):
        try:
            pf = PartFrame.from_dict(frame)
        except (ValueError, TypeError, KeyError) as e:
            state = {"readable": False, "out_of_date": True,
                     "out_of_date_reasons": [f"this version cannot read the part's frame: {e}"]}
    prep.frame = {**frame, **state}
    prep.part_frame = pf
    for w in _text_list(frame.get("warnings")):
        prep.warn("frame", None, w)
    if not state.get("readable", True):
        for w in _text_list(state.get("out_of_date_reasons")):
            prep.warn("frame", None, w)
    elif state.get("out_of_date"):
        reasons = "; ".join(_text_list(state.get("out_of_date_reasons")))
        prep.warn("frame", None, "The part's frame is out of date" + (f": {reasons}" if reasons else "")
                  + ". Build it again from the current fits.")


def _features(data: dict, prep: Prepared) -> None:
    doc = data.get("features")
    if not isinstance(doc, dict):
        return
    _frame(doc, prep)
    want = bool(data.get("numbers_in_part_frame"))
    pf = prep.part_frame if want else None
    prep.in_part_frame = pf is not None
    prep.part_frame_unreadable = want and pf is None and prep.frame is not None
    current = doc.get("mesh_version") if isinstance(doc.get("mesh_version"), int) else None
    feats = [f for f in _list(doc.get("features")) if isinstance(f, dict)]
    prep.features = [_feature_view(f, current, pf, prep) for f in feats]
    for m in _list(doc.get("measurements")):
        if not isinstance(m, dict):
            continue
        a, b = m.get("a"), m.get("b")
        if not (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == 3 and len(b) == 3):
            continue
        A, B = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
        if pf is not None:
            A, B = pf.points_to_part(A), pf.points_to_part(B)
        stale = m.get("stale")
        if stale is None:
            stale = current is not None and m.get("mesh_version") is not None and m.get("mesh_version") != current
        name = str(m.get("name") or m.get("id") or "Distance")
        view = {"id": m.get("id"), "name": name, "a": [float(x) for x in A], "b": [float(x) for x in B],
                "distance_mm": float(np.linalg.norm(B - A)), "stale": bool(stale), "notes": []}
        if stale:
            view["notes"].append("Measured on an earlier mesh: measure it again on the current one.")
            prep.warn("features", name, view["notes"][-1])
        prep.measurements.append(view)
    by_id = {f.get("id"): f for f in feats}
    moves = {m.get("id"): m for m in _list(data.get("cad_moves")) if isinstance(m, dict)}
    prep.relations = [_relation_view(r, by_id, current, moves, prep)
                      for r in _list(doc.get("relations")) if isinstance(r, dict)]


# --------------------------------------------------------------------------- the other parts' warnings
HEALTH_WORDS = {"valid": "Valid", "check": "Check recommended", "check_recommended": "Check recommended",
                "recalibrate": "Recalibration required", "recalibration_required": "Recalibration required"}


def _calibration_warnings(cal: dict, prep: Prepared) -> None:
    missing = _text_list(cal.get("lasers_missing"))
    if missing:
        prep.warn("calibration", None, f"No calibration for the {', '.join(missing)} laser line"
                  f"{'s' if len(missing) > 1 else ''} this build has: calibrate the lasers again.")
    last = cal.get("last_check")
    if isinstance(last, dict) and last.get("ok") is False:
        prep.warn("calibration", "Board check", str(last.get("verdict") or "the last board check did not pass"))
    health = cal.get("health")
    if isinstance(health, dict):
        state = str(health.get("state") or "")
        if state != "valid":
            label = HEALTH_WORDS.get(state, state or "Not known")
            part = f" ({health['part']})" if health.get("part") else ""
            reason = f": {health['reason']}" if health.get("reason") else ""
            prep.warn("calibration", "Health", f"{label}{part}{reason}")


def _comparison_warnings(c: dict, prep: Prepared) -> None:
    ref = c.get("reference") if isinstance(c.get("reference"), dict) else {}
    al = c.get("alignment") if isinstance(c.get("alignment"), dict) else {}
    for w in _text_list(ref.get("warnings")):
        prep.warn("comparison", "Reference", w)
    units = ref.get("units") if isinstance(ref.get("units"), dict) else None
    if units is not None and units.get("status") == "pending":
        prep.warn("comparison", "Reference", f"Its units are not settled: {units.get('message') or ''}".rstrip(": "))
    if al and al.get("accepted") is False:
        prep.warn("comparison", "Alignment", f"The alignment was not accepted: {al.get('verdict') or ''}".rstrip(": "))
    for w in _text_list(al.get("warnings")):
        prep.warn("comparison", "Alignment", w)


def _flush_warnings(g: dict, prep: Prepared) -> None:
    fl = g.get("flush") if isinstance(g.get("flush"), dict) else {}
    n, k = fl.get("n_stations"), fl.get("n_trusted")
    if isinstance(n, int) and isinstance(k, int) and n > 0 and k < n:
        prep.warn("flush", str(g.get("name")) if g.get("name") else None,
                  f"The flush is not trusted at {n - k} of {n} stations along the seam (a surface missing, or a "
                  "reading far from its neighbours): they are left out of the lowest, mean and highest.")


def prepare(data: dict) -> Prepared:
    """Make the input ready to show (module docstring): the kept features worked out and every
    warning collected, in the order of the report's parts. ``data`` is not changed."""
    if not isinstance(data, dict):
        raise ValueError("the report's data must be a dict (see ohs.report.data)")
    prep = Prepared(data=data)
    cal = data.get("calibration")
    if isinstance(cal, dict):
        _calibration_warnings(cal, prep)
    _features(data, prep)
    for s in _list(data.get("sections")):
        if isinstance(s, dict):
            for w in _text_list(s.get("warnings")):
                prep.warn("sections", str(s.get("name") or "Section"), w)
    if isinstance(data.get("comparison"), dict):
        _comparison_warnings(data["comparison"], prep)
    if isinstance(data.get("gap_flush"), dict):
        _flush_warnings(data["gap_flush"], prep)
    order = [p for p, _ in PARTS]
    prep.warnings.sort(key=lambda w: order.index(w.part) if w.part in order else len(order))  # stable
    return prep
