"""The kept fits for CAD over the API (FEATURE_PLAN C2; APP_API.md "Kept features", "For CAD").

Three read-only routes under ``/api/projects/{pid}/scans/{sid}/features/cad``:

* ``GET .../features/cad``: what would be exported (:meth:`CadExport.summary`): how many fits of
  each kind, in which frame, the writer's notes and the warnings, the files' sizes and names, so
  the viewer can show it before the download;
* ``GET .../features/cad/fits.step``: the STEP file (:func:`ohs.cad.step.step_text`);
* ``GET .../features/cad/fits.FCMacro``: the FreeCAD macro (:func:`ohs.cad.fcmacro.macro_text`).

All three take ``boundary`` (``rectangle``, the default, or ``outline``: each plane bounded by its
real outline, :func:`ohs.cad.shapes.cad_shapes`) and ``measured_only`` (below). The shapes are made from the scan's kept fits as
``features.json`` holds them (every fit, in its order), each plane's stored outline (only one kept
for the fit's own mesh version) and the part's frame when the scan has one this version can read.
The files are named after the part (the project's name) inside, and after the scan outside (their
download names), so the STEP file and the macro of two scans cannot be mistaken for each other.

**Approved values** (FEATURE_PLAN C3; :mod:`ohs.server.features`, "nominal values"). An approved
**size** (a cylinder's or a hole's diameter, or its radius) goes into the exported shape:
:func:`ohs.cad.nominal.apply_nominal` makes a copy of the fit's shape with it before
:func:`~ohs.cad.shapes.cad_shapes`, for the STEP file and the macro alike, and the files' notes say
"<name>: nominal diameter 14 mm (measured 14.02 +/- 0.03 mm)" (plain ASCII, so the macro's comments
read as they are). An approval marked "check again" (the fit measured again no longer allows it) is
used too, with a warning.

An approved **relation** (parallel, perpendicular, an angle, a distance, coaxial, in one plane) is
by default listed only: named in the notes as approved, "not applied to the geometry", and no shape
moves; the files are then exactly those of before moves existed. When the user chose that one of
its two fits moves (the relation's ``move``, :meth:`FeatureManager.set_move
<ohs.server.features.FeatureManager.set_move>`), :func:`ohs.cad.moves.apply_moves` turns and slides
a copy of that fit (after the approved sizes are in) to suit the approved values, in the order the
moves were chosen, by its rules (a shape is moved by one relation only, and a shape an applied
relation was measured against stays where it is). The notes then say "applied by moving <fit>" and,
in a note of its own, how far the fit was turned and slid. A move that cannot be applied (a conflict,
a turn with no one nearest way) is not applied: the relation stays listed, "not applied to the
geometry: <why>", with a warning; one whose approval is marked "check again" is applied, with a
warning. A listed relation whose fit another relation moved gets a note with what it reads in the
export. The part's frame is the one built from the measured fits, as before.

``measured_only`` (a query option of all three routes) leaves every approval and every move out:
the files are then exactly those of a scan without approvals, as is every file of a scan that has
none. The measured shapes in ``features.json`` are never changed.

**Kept as kept.** A fit made on an earlier mesh is exported too, it is what the user kept, and the summary says so; an
out-of-date frame is used as it is (as the mesh in the part's frame is), and said so; a frame this
version cannot read is not used, and said so. A scan with no kept fits answers 409 with a clear
message for the files (never an empty file), and a summary with ``reason``.

**Nothing on disk changes:** the files are made in memory on each request from what is read (the
writers give text; no temporary file is needed) and nothing is written.
"""
from __future__ import annotations

import re
from typing import Any

from ohs.server.protocol import ApiError

__all__ = ["CadExport", "CAD_FILES", "BOUNDARIES"]

#: the files, by their name in the route: (their kind, the download name's ending, the media type)
CAD_FILES = {"fits.step": ("step", "-fits.step", "model/step"),
             "fits.FCMacro": ("macro", "-fits.FCMacro", "text/x-python")}
BOUNDARIES = ("rectangle", "outline")
_NO_FITS = "this scan has no kept fits to export"
_NO_FITS_HINT = ("fit planes, cylinders or holes in the viewer first (each fit is kept with the scan), "
                 "then download them for CAD")


_RELATIONS_NOTE = ("approved relations listed only (the default) are named in the notes and not applied to the geometry: "
                   "choose which of a relation's two fits moves to suit it, and the export moves a copy of that fit "
                   "(the measured fits stay as they are)")


def _num(x: float) -> str:
    """A nominal value as written in the notes: 14, 14.5, 10.25."""
    return f"{float(x):.6f}".rstrip("0").rstrip(".")


def _measured(x: float | None, sigma: float | None, unit: str) -> str:
    """A measured value as the notes write it, in plain ASCII: "14.02 +/- 0.03 mm"."""
    if x is None:
        return "not measured now"
    return f"{x:.2f}" + ("" if sigma is None else f" +/- {sigma:.2f}") + unit


def _file_stem(name: str) -> str:
    """A scan's name as the start of a download name: letters, digits, ``.``, ``-`` and ``_``."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("._-")
    return stem[:80] or "scan"


class CadExport:
    """The CAD routes of every scan, on the kept features of the
    :class:`~ohs.server.features.FeatureManager` ``feats`` (read with its
    :meth:`~ohs.server.features.FeatureManager.snapshot`)."""

    def __init__(self, feats: Any) -> None:
        self.feats = feats
        self.st = feats.st

    def _nominals(self, pid: str, sid: str, doc: dict, measured_only: bool, outlines: dict | None = None) -> dict:
        """The approved nominal values the export uses (module docstring, "Approved values"): the
        fits to export (a copy of each with an approved size, :func:`ohs.cad.nominal.apply_nominal`,
        and of each fit a relation moves, :func:`ohs.cad.moves.apply_moves`), the planes' outlines
        (a moved plane's moved with it), the notes for the files, the warnings, and what the summary
        says of them."""
        from ohs.cad.moves import apply_moves
        from ohs.cad.nominal import apply_nominal

        out = {"features": doc["features"], "outlines": outlines or {}, "notes": [], "warnings": [],
               "summary": {"measured_only": measured_only, "sizes_used": 0, "sizes": [], "relations_listed": 0,
                           "relations_applied": 0, "relations": [], "relations_note": None, "moves": []}}
        if measured_only or not any(it.get("nominals") for key in ("features", "relations") for it in doc[key]):
            return out
        views = self.feats.nominal_views(doc, self.feats.poses(pid, sid)[0])
        summary = out["summary"]
        features = []
        for f in doc["features"]:
            size = next((n for n in views.get(f["id"], []) if n["value"] in ("diameter", "radius")), None)
            if size is None or f["kind"] not in ("cylinder", "hole"):
                features.append(f)
                continue
            key = "diameter_mm" if size["value"] == "diameter" else "radius_mm"
            features.append({**f, "shape": apply_nominal(f["shape"], **{key: size["nominal"]})})
            out["notes"].append(f"{f['name']}: nominal {size['value']} {_num(size['nominal'])} mm "
                                f"(measured {_measured(size['measured_now'], size['sigma_now'], ' mm')})")
            summary["sizes"].append({"id": f["id"], "name": f["name"], "value": size["value"],
                                     "nominal": size["nominal"], "measured": size["measured_now"],
                                     "sigma": size["sigma_now"], "check_again": size["check_again"]})
            if size["check_again"]:
                why = (size["check_reason"] or "").removeprefix("check again: ")
                out["warnings"].append(f"\"{f['name']}\": its nominal {size['value']} {_num(size['nominal'])} mm is "
                                       f"marked check again ({why}): used as approved; approve it again or remove it")
        by_id = {f["id"]: f for f in doc["features"]}
        # the moves, in the order they were chosen (ohs.cad.moves); none: every relation is listed only
        moving = sorted((r for r in doc["relations"] if r.get("move") and views.get(r["id"])),
                        key=lambda r: int(r["move"]["order"]))
        moves = {}
        if moving:
            done = apply_moves(features, [{**r, "nominals": views[r["id"]]} for r in moving], out["outlines"])
            features, out["outlines"] = done.features, done.outlines
            moves = {m.id: m for m in done.moves}
        moved_by = {m.moved_id: m for m in moves.values() if m.applied}
        exported = {f["id"]: f for f in features}
        for r in doc["relations"]:
            mv = moves.get(r["id"])
            for n in views.get(r["id"], []):
                a, b = by_id[r["a"]]["name"], by_id[r["b"]]["name"]
                unit = " deg" if n["unit"] == "deg" else " mm"
                what = n["word"] or f"{n['value'].replace('_', ' ')} {_num(n['nominal'])}{unit.rstrip()}"
                if mv is not None and mv.applied:
                    how = f"applied by moving {mv.moved_name}"
                elif mv is not None:
                    how = f"not applied to the geometry: {mv.reason}"
                else:
                    how = "not applied to the geometry"
                out["notes"].append(f"{r['name']} ({a} and {b}): approved {what} (measured {n['value'].replace('_', ' ')} "
                                    f"{_measured(n['measured_now'], n['sigma_now'], unit)}); {how}")
                summary["relations"].append({"id": r["id"], "name": r["name"], "value": n["value"],
                                             "nominal": n["nominal"], "word": n["word"], "check_again": n["check_again"],
                                             "move": (r.get("move") or {}).get("shape", "listed"),
                                             "applied": bool(mv is not None and mv.applied)})
            if mv is not None and mv.applied:
                out["notes"].append(f"{r['name']}: {mv.sentence} (in the export only; the measured fit is unchanged)")
            elif mv is not None:
                out["warnings"].append(f"\"{r['name']}\": {mv.moved_name or 'its fit'} is not moved ({mv.reason}): the "
                                       "relation is listed in the notes only")
            elif views.get(r["id"]):
                others = [moved_by[x] for x in (r["a"], r["b"]) if x in moved_by]
                if others:
                    out["notes"].append(self._listed_after_moves(r, others, exported, views[r["id"]]))
            if mv is not None:
                out["warnings"].extend(mv.warnings)
                summary["moves"].append(mv.to_dict())
        out["features"] = features
        summary["sizes_used"] = len(summary["sizes"])
        summary["relations_applied"] = sum(x["applied"] for x in summary["relations"])
        summary["relations_listed"] = len(summary["relations"]) - summary["relations_applied"]
        if summary["relations_listed"]:
            summary["relations_note"] = _RELATIONS_NOTE
        return out

    @staticmethod
    def _listed_after_moves(r: dict, others: list, exported: dict, approvals: list[dict]) -> str:
        """The note of a listed relation one of whose fits another relation moved: what it reads in
        the export (it was approved against the measured fits)."""
        from ohs.measure.relate import relate

        fa, fb = exported[r["a"]], exported[r["b"]]
        rel = relate({"kind": fa["kind"], "shape": fa["shape"]}, {"kind": fb["kind"], "shape": fb["shape"]})
        reads = []
        for n in approvals:
            q = rel.get(n["value"])
            unit = " deg" if n["unit"] == "deg" else " mm"
            reads.append(f"{n['value'].replace('_', ' ')} " + ("none" if q is None else f"{q.value:.4f}{unit}"))
        by = ", ".join(f"{m.moved_name} by \"{m.name}\"" for m in others)
        return f"{r['name']}: listed only; moved for another relation ({by}), in the export it reads {'; '.join(reads)}"

    def _read(self, pid: str, sid: str, boundary: str, measured_only: bool = False) -> dict:
        """The scan's kept fits as shapes to export, with what the summary says of them."""
        import ohs.measure.features as kf
        from ohs.cad.shapes import cad_shapes

        if boundary not in BOUNDARIES:
            raise ApiError(422, f"boundary must be 'rectangle' or 'outline', got {boundary!r}",
                           "leave it out for each plane's rectangle, or give 'outline' for its real outline")
        project = self.st.projects.get(pid)  # 404 for an unknown project
        scan = self.st.projects.scan_entry(pid, sid)  # ... or scan
        doc, outlines, version = self.feats.snapshot(pid, sid, plane_outlines=boundary == "outline")
        features = doc["features"]
        warnings = []
        stale = [f for f in features if version is None or f["mesh_version"] != version]
        for f in stale:
            warnings.append(f"\"{f['name']}\" was fitted on an earlier mesh (version {f['mesh_version']}"
                            + (f", the mesh is now version {version}" if version is not None else "")
                            + "): exported as it was kept; fit it again for this mesh's numbers")
        frame = None
        state = kf.frame_state(doc["frame"], features)
        if state is not None and not state["readable"]:
            warnings.append(f"{state['out_of_date_reasons'][0]}: the shapes are in the scan's own frame")
        elif state is not None:
            frame = doc["frame"]
            if state["out_of_date"]:
                warnings.append("the part's frame is out of date (" + "; ".join(state["out_of_date_reasons"])
                                + "): used as it is; build it again for the fits as they are now")
        nominal = self._nominals(pid, sid, doc, measured_only, outlines)
        warnings.extend(nominal["warnings"])
        shapes = None
        if features:
            try:
                shapes = cad_shapes(nominal["features"], outlines=nominal["outlines"], frame=frame, boundary=boundary)
            except ValueError as e:
                raise ApiError(409, f"the kept fits cannot be exported: {e}",
                               "fit that fit again, or delete it, then download again") from None
            shapes.notes.extend(nominal["notes"])
        return {"project": project, "scan": scan, "features": features, "outlines": outlines, "shapes": shapes,
                "boundary": boundary, "version": version, "stale": [f["id"] for f in stale], "warnings": warnings,
                "frame": frame, "nominal": nominal["summary"]}

    @staticmethod
    def _texts(r: dict) -> tuple[str, str]:
        from ohs.cad.fcmacro import macro_text
        from ohs.cad.step import step_text

        title = r["project"]["name"]
        return step_text(r["shapes"], title=title), macro_text(r["shapes"], title=title)

    @staticmethod
    def _filenames(scan: dict) -> dict:
        stem = _file_stem(scan.get("name") or scan.get("id"))
        return {kind: stem + end for kind, end, _ in CAD_FILES.values()}

    # ------------------------------------------------------------------ routes
    def summary(self, pid: str, sid: str, boundary: str = "rectangle", measured_only: bool = False) -> dict:
        """``GET .../features/cad``: what the two files would hold.

        ``{"boundary", "n_fits", "counts": {"plane", "cylinder", "hole", "point"}, "frame": "part" |
        "scan", "frame_label", "notes" (the writer's), "warnings" (fits made on an earlier mesh, a
        frame out of date or not readable), "stale" (those fits' ids), "outline" (with
        ``boundary=outline``: how many planes, points and holes the faces hold and how many of the
        outlines' holes were left out; else null), "step_bytes", "macro_bytes", "filenames":
        {"step", "macro"}, "mesh_version", "reason", "nominal"}``: ``reason`` says why nothing can be
        exported (no kept fits; then the counts are 0 and the sizes null), else null. ``nominal``
        (module docstring, "Approved values"): ``{"measured_only", "sizes_used", "sizes": [{"id",
        "name", "value", "nominal", "measured", "sigma", "check_again"}], "relations_listed" (approved
        relation values only listed), "relations_applied" (applied by a move), "relations": [{"id",
        "name", "value", "nominal", "word", "check_again", "move" ("listed", "a" or "b"), "applied"}],
        "relations_note", "moves": [:meth:`ohs.cad.moves.Move.to_dict`, in the order applied]}``."""
        from ohs.cad.shapes import CylinderFace, HoleCircle, PlaneFace, PointMark

        r = self._read(pid, sid, boundary, measured_only)
        shapes = r["shapes"]
        counts = {"plane": 0, "cylinder": 0, "hole": 0, "point": 0}
        out = {"boundary": boundary, "n_fits": len(r["features"]), "counts": counts,
               "frame": "part" if r["frame"] is not None else "scan",
               "frame_label": "the part's frame" if r["frame"] is not None else "the scan's own frame",
               "notes": [], "warnings": r["warnings"], "stale": r["stale"], "outline": None, "step_bytes": None,
               "macro_bytes": None, "filenames": self._filenames(r["scan"]), "mesh_version": r["version"],
               "reason": None, "nominal": r["nominal"]}
        if shapes is None:
            out["reason"] = f"{_NO_FITS}: {_NO_FITS_HINT}"
            return out
        kind = {PlaneFace: "plane", CylinderFace: "cylinder", HoleCircle: "hole", PointMark: "point"}
        for s in shapes.shapes:
            counts[kind[type(s)]] += 1
        out["notes"] = list(shapes.notes)
        if boundary == "outline":
            planes = [s for s in shapes.shapes if isinstance(s, PlaneFace) and s.id in r["outlines"]]
            kept = sum(len(s.holes) for s in planes)
            out["outline"] = {"planes": len(planes),
                              "points": sum(len(s.outer) + sum(len(h) for h in s.holes) for s in planes),
                              "holes_kept": kept,
                              "holes_left_out": sum(len(r["outlines"][s.id]["holes"]) for s in planes) - kept}
        step, macro = self._texts(r)
        out["step_bytes"], out["macro_bytes"] = len(step), len(macro)
        return out

    def file(self, pid: str, sid: str, name: str, boundary: str = "rectangle",
             measured_only: bool = False) -> tuple[bytes, str, str, int | None]:
        """``GET .../features/cad/{name}`` (``fits.step`` or ``fits.FCMacro``): the file's bytes, its
        download name, its media type and the mesh version read with the kept fits. 404 for another
        name, 409 when the scan has no kept fits (or one that cannot be exported). With
        ``measured_only`` the approved values are left out: the measured shapes only."""
        if name not in CAD_FILES:
            raise ApiError(404, f"no CAD file {name!r}", "available: " + ", ".join(CAD_FILES))
        r = self._read(pid, sid, boundary, measured_only)
        if r["shapes"] is None:
            raise ApiError(409, _NO_FITS, _NO_FITS_HINT)
        kind, _, media = CAD_FILES[name]
        step, macro = self._texts(r)
        text = step if kind == "step" else macro
        return text.encode("ascii"), self._filenames(r["scan"])[kind], media, r["version"]
